import asyncio
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, render_template, jsonify, request, flash, redirect, url_for, abort
import datetime, requests
from datetime import datetime
import pymssql, shopify
import aiohttp
import lazop
import aiohttp

app = Flask(__name__)
app.debug = True
app.secret_key = os.getenv('APP_SECRET_KEY', 'default_secret_key')  # Use environment variable

order_details = []
@app.route("/submit_tasks", methods=["POST"])
def submit_tasks():
    conn = get_db_connection()
    cursor = conn.cursor()

    today = datetime.date.today()
    cursor.execute("SELECT * FROM tasks WHERE Date = %s", (today,))
    existing_tasks = cursor.fetchone()

    if existing_tasks:
        flash("Tasks have already been submitted for today.", "error")
        return redirect(url_for("index"))

    selected_tasks = {}
    task_names = ["Confirm_Pending_Orders", "Track_Dispatched_Orders", "Contact_Abandoned_Orders", "Email_Courier",
                  "Track_Daraz_Orders", "Solve_Customer_Complaints", "Call_Delivered_Orders", "Add_Reviews_to_Shopify",
                  "Add_Reviews_to_Google_Maps", "Answer_Whatsapp_Messages_Calls", "Answer_Instagram_Facebook_Messages_Comments",
                  "Answer_Daraz_Messages", "Answer_Phone_Calls"]

    for task_name in task_names:
        selected_tasks[task_name] = 1 if task_name in request.form else 0

    cursor.execute("""
        INSERT INTO tasks (Date, Confirm_Pending_Orders, Track_Dispatched_Orders, Contact_Abandoned_Orders, Email_Courier,
        Track_Daraz_Orders, Solve_Customer_Complaints, Call_Delivered_Orders, Add_Reviews_to_Shopify,
        Add_Reviews_to_Google_Maps, Answer_Whatsapp_Messages_Calls, Answer_Instagram_Facebook_Messages_Comments,
        Answer_Daraz_Messages, Answer_Phone_Calls)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (today, *selected_tasks.values()))

    conn.commit()
    conn.close()

    flash("Tasks submitted successfully!", "success")
    return redirect(url_for("index"))

@app.route('/send-email', methods=['POST'])
def send_email():
    data = request.get_json()
    to_emails = data.get('to', [])
    cc_emails = data.get('cc', [])
    subject = data.get('subject', '')
    body = data.get('body', '')

    try:
        # SMTP server configuration
        smtp_server = 'smtp.gmail.com'
        smtp_port = 587
        smtp_user = os.getenv('SMTP_USER')  # Use environment variable
        smtp_password = os.getenv('SMTP_PASSWORD')  # Use environment variable

        # Create the message
        msg = MIMEText(body)
        msg['From'] = smtp_user
        msg['To'] = ', '.join(to_emails)
        msg['Cc'] = ', '.join(cc_emails)
        msg['Subject'] = subject

        # Connect to the SMTP server and send email
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to_emails + cc_emails, msg.as_string())
        server.quit()

        return jsonify({'message': 'Email sent successfully'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

async def fetch_tracking_data(session, tracking_number):

    url = f"https://cod.callcourier.com.pk/api/CallCourier/GetTackingHistory?cn={tracking_number}"
    async with session.get(url) as response:
        return await response.json()

async def process_line_item(session, line_item, fulfillments):
    if line_item.fulfillment_status is None and line_item.fulfillable_quantity == 0:
        return []

    tracking_info = []

    if line_item.fulfillment_status == "fulfilled":
        for fulfillment in fulfillments:
            if fulfillment.status == "cancelled":
                continue
            for item in fulfillment.line_items:
                if item.id == line_item.id:
                    tracking_number = fulfillment.tracking_number
                    data = await fetch_tracking_data(session, tracking_number)

                    if data:
                        consignment_list = data
                        if consignment_list:
                            tracking_details = consignment_list
                            if tracking_details:
                                try:
                                    final_status = tracking_details[-1].get('ProcessDescForPortal')
                                except:
                                    final_status = 'N/A'

                            else:
                                final_status = "Booked"
                                print("No tracking details available.")
                        else:
                            final_status = "Booked"
                            print("No packets found.")
                    else:
                        final_status = "N/A"
                        print("Error fetching data.")

                    # Track quantity for each tracking number
                    tracking_info.append({
                        'tracking_number': tracking_number,
                        'status': final_status,
                        'quantity': item.quantity
                    })

    return tracking_info if tracking_info else [
        {"tracking_number": "N/A", "status": "Un-Booked", "quantity": line_item.quantity}]

async def process_order(session, order):
    order_start_time = time.time()

    input_datetime_str = order.created_at
    parsed_datetime = datetime.fromisoformat(input_datetime_str[:-6])
    formatted_datetime = parsed_datetime.strftime("%b %d, %Y")

    try:
        status = (order.fulfillment_status).title()
    except:
        status = "Un-fulfilled"
    print(order)
    tags = []
    try:
        name = order.billing_address.name
    except AttributeError:
        name = " "
        print("Error retrieving name")

    try:
        address = order.billing_address.address1
    except AttributeError:
        address = " "
        print("Error retrieving address")

    try:
        city = order.billing_address.city
    except AttributeError:
        city = " "
        print("Error retrieving city")

    try:
        phone = order.billing_address.phone
    except AttributeError:
        phone = " "
        print("Error retrieving phone")

    customer_details = {
        "name": name,
        "address": address,
        "city": city,
        "phone": phone
    }
    order_info = {
        'order_id': order.order_number,
        'tracking_id': 'N/A',
        'created_at': formatted_datetime,
        'total_price': order.total_price,
        'line_items': [],
        'financial_status': (order.financial_status).title(),
        'fulfillment_status': status,
        'customer_details' : customer_details,
        'tags': order.tags.split(", "),
        'id': order.id
    }
    print(order.tags)

    tasks = []
    for line_item in order.line_items:
        tasks.append(process_line_item(session, line_item, order.fulfillments))

    results = await asyncio.gather(*tasks)
    variant_name = ""
    for tracking_info_list, line_item in zip(results, order.line_items):
        if tracking_info_list is None:
            continue

        if line_item.product_id is not None:
            product = shopify.Product.find(line_item.product_id)
            if product and product.variants:
                for variant in product.variants:
                    if variant.id == line_item.variant_id:
                        if variant.image_id is not None:
                            images = shopify.Image.find(image_id=variant.image_id, product_id=line_item.product_id)
                            variant_name = line_item.variant_title
                            for image in images:
                                if image.id == variant.image_id:
                                    image_src = image.src
                        else:
                            variant_name = ""
                            image_src = product.image.src
        else:
            image_src = "https://static.thenounproject.com/png/1578832-200.png"

        for info in tracking_info_list:
            order_info['line_items'].append({
                'fulfillment_status': line_item.fulfillment_status,
                'image_src': image_src,
                'product_title': (line_item.title or "") + " - " + (variant_name or ""),
                'quantity': info['quantity'],
                'tracking_number': info['tracking_number'],
                'status': info['status']
            })
            order_info['status'] = info['status']

    order_end_time = time.time()
    print(f"Time taken to process order {name} {order.order_number}: {order_end_time - order_start_time:.2f} seconds")

    return order_info



@app.route('/apply_tag', methods=['POST'])
def apply_tag():
    data = request.json
    order_id = data.get('order_id')
    tag = data.get('tag')

    # Get today's date in YYYY-MM-DD format
    today_date = datetime.now().strftime('%Y-%m-%d')
    tag_with_date = f"{tag.strip()} ({today_date})"

    try:
        # Fetch the order
        order = shopify.Order.find(order_id)

        # If the tag is "Returned", cancel the order
        if tag.strip().lower() == "returned":
            # Attempt to cancel the order
            if order.cancel():
                print("Order Cancelled")
            else:
                print("Order Cancellation Failed")
        if tag.strip().lower() == "delivered":
            if order.close():
                print("Order Cloed")
            else:
                print("Order Closing Failed")

        # Process existing tags
        if order.tags:
            tags = [t.strip() for t in order.tags.split(", ")]  # Remove excess spaces
        else:
            tags = []

        # Remove a specific tag if needed (e.g., "Leopards Courier")
        if "Leopards Courier" in tags:
            tags.remove("Leopards Courier")

        # Add new tag if it doesn't already exist
        if tag_with_date not in tags:
            tags.append(tag_with_date)

        # Update the order with the new tags
        order.tags = ", ".join(tags)

        # Save the order
        if order.save():
            return jsonify({"success": True, "message": "Tag applied successfully."})
        else:
            return jsonify({"success": False, "error": "Failed to save order changes."})

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"success": False, "error": str(e)})

async def getShopifyOrders():

    global order_details
    orders = shopify.Order.find(limit=250, order='created_at DESC')
    order_details = []
    total_start_time = time.time()

    async with aiohttp.ClientSession() as session:
        tasks = [process_order(session, order) for order in orders]
        order_details = await asyncio.gather(*tasks)

    total_end_time = time.time()
    print(f"Total time taken to process all orders: {total_end_time - total_start_time:.2f} seconds")

    return order_details

@app.route("/track")
def tracking():
    global order_details
    return render_template("track.html", order_details=order_details)

@app.route("/")
def tracking2():
    global order_details
    return render_template("track.html", order_details=order_details)


def get_daraz_orders(statuses):
    try:
        access_token = '50000601237osiZ0F1HkTZVojWcjq6szVDmDPjxiuvoEbCSvB15ff2bc8xtn4m'
        client = lazop.LazopClient('https://api.daraz.pk/rest', '501554', 'nrP3XFN7ChZL53cXyVED1yj4iGZZtlcD')

        all_orders = []

        for status in statuses:
            request = lazop.LazopRequest('/orders/get', 'GET')
            request.add_api_param('sort_direction', 'DESC')
            request.add_api_param('update_before', '2025-02-10T16:00:00+08:00')
            request.add_api_param('offset', '0')
            request.add_api_param('created_before', '2025-02-10T16:00:00+08:00')
            request.add_api_param('created_after', '2017-02-10T09:00:00+08:00')
            request.add_api_param('limit', '50')
            request.add_api_param('update_after', '2017-02-10T09:00:00+08:00')
            request.add_api_param('sort_by', 'updated_at')
            request.add_api_param('status', status)
            request.add_api_param('access_token', access_token)

            response = client.execute(request)
            darazOrders = response.body.get('data', {}).get('orders', [])

            for order in darazOrders:
                print(order)
                order_id = order.get('order_id', 'Unknown')

                item_request = lazop.LazopRequest('/order/items/get', 'GET')
                item_request.add_api_param('order_id', order_id)
                item_request.add_api_param('access_token', access_token)

                item_response = client.execute(item_request)
                try:
                    items = item_response.body.get('data', [])
                    if not items:
                        raise ValueError("No items found in the response.")
                except (AttributeError, ValueError) as e:
                    print(f"Error retrieving items: {e}")
                    items = []

                item_details = []
                for item in items:
                    tracking_num = item.get('tracking_code', 'Unknown')

                    tracking_req = lazop.LazopRequest('/logistic/order/trace', 'GET')
                    tracking_req.add_api_param('order_id', order_id)
                    tracking_req.add_api_param('access_token', access_token)
                    tracking_response = client.execute(tracking_req)

                    tracking_data = tracking_response.body.get('result', {})
                    packages = tracking_data.get('data', [{}])[0].get('package_detail_info_list', [])

                    track_status = "N/A"
                    for package in packages:
                        if package.get("tracking_number") == tracking_num:
                            try:
                                track_status = package.get('logistic_detail_info_list', [{}])[-1].get('title', "N/A")
                            except (IndexError, KeyError) as e:
                                print(f"Error processing tracking data: {e}")
                                track_status = "N/A"
                            print("MATCHED")
                            break

                    item_detail = {
                        'item_image': item.get('product_main_image', 'N/A'),
                        'item_title': item.get('name', 'Unknown'),
                        'quantity': item.get('variation', 'N/A'),
                        'tracking_number': item.get('tracking_code', 'N/A'),
                        'status': track_status
                    }
                    item_details.append(item_detail)

                filtered_order = {
                    'order_id': order.get('order_id', 'Unknown'),
                    'customer': {
                        'name': f"{order.get('customer_first_name', 'Unknown')} {order.get('customer_last_name', 'Unknown')}",
                        'address': order.get('address_shipping', {}).get('address', 'N/A'),
                        'phone': order.get('address_shipping', {}).get('phone', 'N/A')
                    },
                    'status': status.replace('_', ' ').title(),
                    'date': format_date(order.get('created_at', 'N/A')),
                    'total_price': order.get('price', '0.00'),
                    'items_list': item_details,
                }
                all_orders.append(filtered_order)

        return all_orders
    except Exception as e:
        print(f"Error fetching darazOrders: {e}")
        return []


@app.route('/daraz')
def daraz():
    statuses = ['shipped','pending','ready_to_ship']
    darazOrders = get_daraz_orders(statuses)
    return render_template('daraz.html', darazOrders=darazOrders)

def format_date(date_str):
    # Parse the date string
    date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
    # Format the date object to only show the date
    return date_obj.strftime("%Y-%m-%d")

@app.route('/refresh', methods=['POST'])
def refresh_data():
    global order_details
    try:
        order_details = asyncio.run(getShopifyOrders())
        return jsonify({'message': 'Data refreshed successfully'})
    except Exception as e:
        print(f"Error refreshing data: {e}")
        return jsonify({'message': 'Failed to refresh data'}), 500


def run_async(func, *args, **kwargs):
    return asyncio.run(func(*args, **kwargs))
@app.route('/track/<tracking_num>')
def displayTracking(tracking_num):
    print(f"Tracking Number: {tracking_num}")  # Debug line

    async def async_func():
        async with aiohttp.ClientSession() as session:
            return await fetch_tracking_data(session, tracking_num)

    data = run_async(async_func)

    return render_template('trackingdata.html', data=data)




@app.route('/accounts/<account_name>')
def accountData(account_name):
    print(f"Account Name: {account_name}")  # Debug line

    connection = check_database_connection()
    if connection is None:
        return "Database connection error", 500  # Return an error message or page if connection fails

    print("CONNECTED TO DATABASE")

    try:
        with connection.cursor(as_dict=True) as cursor:
            query = "SELECT * FROM AODIncomeExpenseTable WHERE Income_Expense_Name LIKE %s ORDER BY Payment_Date DESC"
            cursor.execute(query, ('%' + account_name + '%',))
            transactions = cursor.fetchall()
    except pymssql.Error as e:
        print(f"Error fetching data from the database: {str(e)}")
        transactions = []  # Ensure transactions is defined
    finally:
        connection.close()

    # Simple template rendering to verify if template works without data
    return render_template('finance_report.html', transactions=transactions)

@app.route('/expense_data')
def expense_data():
    connection = check_database_connection()

    try:
        if connection:
            cursor = connection.cursor()
            cursor.execute("""
                SELECT e.expense_id, e.expense_title, s.subtype_title
                FROM AODExpenseTypes e
                LEFT JOIN AODExpenseSubtypes s ON e.expense_id = s.expense_id
            """)
            rows = cursor.fetchall()

            expense_data = {}
            for expense_id, expense_title, subtype_title in rows:
                if expense_id not in expense_data:
                    expense_data[expense_id] = {
                        "expense_title": expense_title,
                        "subtypes": []
                    }
                if subtype_title:
                    expense_data[expense_id]["subtypes"].append(subtype_title)

            # Convert the dictionary to the format needed
            response_data = {
                'types': [{'expense_id': k, 'expense_title': v['expense_title']} for k, v in expense_data.items()],
                'subtypes': {str(k): v['subtypes'] for k, v in expense_data.items()}
            }

            return jsonify(response_data)
        else:
            return "Error: No database connection"

    except Exception as e:
        print(f"Error in expense_data route: {str(e)}")
        return "Error in expense_data route"

    finally:
        if connection:
            connection.close()

from flask import request, jsonify
from datetime import datetime




shop_url = os.getenv('SHOP_URL')
api_key = os.getenv('API_KEY')
password = os.getenv('PASSWORD')
shopify.ShopifyResource.set_site(shop_url)
shopify.ShopifyResource.set_user(api_key)
shopify.ShopifyResource.set_password(password)
order_details = asyncio.run(getShopifyOrders())

if __name__ == "__main__":
    shop_url = os.getenv('SHOP_URL')
    api_key = os.getenv('API_KEY')
    password = os.getenv('PASSWORD')
    shopify.ShopifyResource.set_site(shop_url)
    shopify.ShopifyResource.set_user(api_key)
    shopify.ShopifyResource.set_password(password)

    app.run(port=5002)

