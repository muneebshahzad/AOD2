import asyncio
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Flask, render_template, jsonify, request, flash, redirect, url_for
import datetime, requests
from datetime import datetime
import pymssql, shopify
import aiohttp

app = Flask(__name__)
app.debug = True
app.secret_key = os.getenv('APP_SECRET_KEY', 'default_secret_key')  # Use environment variable

def get_db_connection():
    server = os.getenv('DB_SERVER')
    database = os.getenv('DB_DATABASE')
    username = os.getenv('DB_USERNAME')
    password = os.getenv('DB_PASSWORD')
    try:
        connection = pymssql.connect(server=server, user=username, password=password, database=database)
        return connection
    except pymssql.Error as e:
        print(f"Error connecting to the database: {str(e)}")
        return None

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
    api_key = os.getenv('LEOPARD_API_KEY')  # Use environment variable
    api_password = os.getenv('LEOPARD_PASSWORD')  # Use environment variable
    url = f"https://merchantapi.leopardscourier.com/api/trackBookedPacket/?api_key={api_key}&api_password={api_password}&track_numbers={tracking_number}"
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
                    if data['status'] == 1 and not data['error']:
                        packet_list = data['packet_list']
                        if packet_list:
                            tracking_details = packet_list[0].get('Tracking Detail', [])
                            if tracking_details:
                                final_status = tracking_details[-1]['Status']
                                keywords = ["Return", "hold", "UNTRACEABLE"]
                                if not any(
                                        kw.lower() in final_status.lower() for kw in
                                        ["delivered", "returned to shipper"]):
                                    for detail in tracking_details:
                                        status = detail['Status']
                                        if any(kw in status for kw in keywords):
                                            final_status = "Being Return"
                                            break
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

    order_info = {
        'order_id': order.order_number,
        'tracking_id': 'N/A',
        'created_at': formatted_datetime,
        'total_price': order.total_price,
        'line_items': [],
        'financial_status': (order.financial_status).title(),
        'fulfillment_status': status,
        'customer_details' : {"name" : order.billing_address.name , "address" : order.billing_address.address1 , "city" : order.billing_address.city , "phone":  order.billing_address.phone}
    }

    tasks = []
    for line_item in order.line_items:
        tasks.append(process_line_item(session, line_item, order.fulfillments))

    results = await asyncio.gather(*tasks)
    for tracking_info_list, line_item in zip(results, order.line_items):
        if tracking_info_list is None:
            continue  # Skip this line item

        if line_item.product_id is not None:
            product = shopify.Product.find(line_item.product_id)
            if product and product.variants:
                for variant in product.variants:
                    if variant.id == line_item.variant_id:
                        if variant.image_id is not None:
                            images = shopify.Image.find(image_id=variant.image_id, product_id=line_item.product_id)
                            for image in images:
                                if image.id == variant.image_id:
                                    image_src = image.src
                        else:
                            image_src = product.image.src
        else:
            image_src = "https://static.thenounproject.com/png/1578832-200.png"

        for info in tracking_info_list:
            order_info['line_items'].append({
                'fulfillment_status': line_item.fulfillment_status,
                'image_src': image_src,
                'product_title': line_item.title,
                'quantity': info['quantity'],
                'tracking_number': info['tracking_number'],
                'status': info['status']
            })

    order_end_time = time.time()
    print(f"Time taken to process order {order.order_number}: {order_end_time - order_start_time:.2f} seconds")

    return order_info

async def getShopifyOrders():
    shop_url = os.getenv('SHOP_URL')  # Use environment variable
    api_key = os.getenv('API_KEY')  # Use environment variable
    password = os.getenv('PASSWORD')  # Use environment variable

    shopify.ShopifyResource.set_site(shop_url)
    shopify.ShopifyResource.set_user(api_key)
    shopify.ShopifyResource.set_password(password)

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
    order_details = asyncio.run(getShopifyOrders())
    return render_template("tables.html", order_details=order_details)

@app.route("/")
def index():
    today = datetime.now().date()

    conn = get_db_connection()
    cursor = conn.cursor(as_dict=True)

    cursor.execute("SELECT * FROM tasks WHERE Date = %s", (today,))
    tasks = cursor.fetchone()

    cursor.execute("SELECT Start FROM daily_sessions WHERE Date = %s", (today,))
    session_result = cursor.fetchone()

    conn.close()

    should_show_start_modal = "true" if not session_result else "false"
    should_show_resume_modal = "false" if should_show_start_modal == "true" else "true"

    start_time = session_result['Start'] if session_result else None

    return render_template("index.html", tasks=tasks, today=today,
                           should_show_start_modal=should_show_start_modal,
                           should_show_resume_modal=should_show_resume_modal,
                           start_time=start_time)

@app.route('/start_timer', methods=['POST'])
def start_timer():
    today = datetime.datetime.now().date()
    start_time = datetime.datetime.now().time()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO daily_sessions (Date, Start) VALUES (%s, %s)", (today, start_time))
    conn.commit()
    conn.close()

    return jsonify({"status": "started"})

@app.route('/resume_timer', methods=['POST'])
def resume_timer():
    today = datetime.datetime.now().date()
    resume_time = datetime.datetime.now().time()

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT MAX(Lap_Number) FROM timer_logs WHERE Date = %s", (today,))
    max_lap = cursor.fetchone()[0]
    new_lap_number = max_lap + 1 if max_lap is not None else 1

    cursor.execute("INSERT INTO timer_logs (Date, Lap_Number, Resume) VALUES (%s, %s, %s)",
                   (today, new_lap_number, resume_time))
    conn.commit()
    conn.close()

    return jsonify({"status": "resumed"})

@app.route('/get_elapsed_time', methods=['GET'])
def get_elapsed_time():
    today = datetime.now().date()

    conn = get_db_connection()
    cursor = conn.cursor(as_dict=True)
    cursor.execute("SELECT Start FROM daily_sessions WHERE Date = %s", (today,))
    session_result = cursor.fetchone()

    if session_result:
        start_time = session_result['Start']
        start_datetime = datetime.datetime.combine(today, start_time)
        current_datetime = datetime.datetime.now()

        cursor.execute("SELECT Resume FROM timer_logs WHERE Date = %s ORDER BY Lap_Number", (today,))
        laps = cursor.fetchall()
        total_pause_time = sum([(current_datetime - datetime.datetime.combine(today, lap['Resume'])).total_seconds() for lap in laps])

        elapsed_seconds = int((current_datetime - start_datetime).total_seconds() - total_pause_time)
    else:
        elapsed_seconds = 0

    conn.close()
    return jsonify({"elapsed_seconds": elapsed_seconds})

if __name__ == "__main__":
    app.run(port=5001)
