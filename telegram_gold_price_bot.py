import json
import threading
import time
import websocket
import telebot
from telebot import types
from telebot.apihelper import ApiException
from collections import defaultdict


# Load bot settings from JSON file
with open("config.json") as bot_settings:
    data = json.load(bot_settings)
    API_TOKEN = data["API_TOKEN"]
    # AUTHORIZED_USERS = [data["USER_ID1"], data["USER_ID2"], data["USER_ID3"]]

bot = telebot.TeleBot(API_TOKEN, threaded=False)

active_users = {}  # To track users who want continuous updates
user_frequencies = defaultdict(lambda: 60)  # Per-user update frequency in seconds
price_alerts = defaultdict(list)  # User alerts: {chat_id: [{'price': float, 'direction': 'above'/'below', 'id': int}]}
alert_counter = 0  # Counter for unique alert IDs
current_price_data = None  # Store full price data as dictionary
current_price = "Waiting for price update..."
ws = None  # WebSocket connection
start_gold_message = r"""["{\"_event\":\"bulk-subscribe\",\"tzID\":8,\"message\":\"pid-68:\"}"]"""

# Format price message with enhanced display
def format_price_message(data):
    """Format price data into a nice display message"""
    try:
        bid = float(data.get('bid', '0').replace(',', ''))
        ask = float(data.get('ask', '0').replace(',', ''))
        last = float(data.get('last_numeric', 0))
        spread = ask - bid
        spread_pct = (spread / bid * 100) if bid > 0 else 0
        
        high = data.get('high', 'N/A')
        low = data.get('low', 'N/A')
        pc = data.get('pc', 'N/A')  # Price change
        pcp = data.get('pcp', 'N/A')  # Price change percentage
        time_str = data.get('time', 'N/A')
        
        # Determine trend emoji
        if 'greenFont' in data.get('pc_col', ''):
            trend = '📈'
        elif 'redBg' in data.get('last_dir', ''):
            trend = '📉'
        else:
            trend = '➡️'
        
        message = f"""🪙 <b>XAUUSD Gold Price</b> {trend}

💰 <b>Last:</b> {data.get('last', 'N/A')}
📊 <b>Bid:</b> {data.get('bid', 'N/A')}
📊 <b>Ask:</b> {data.get('ask', 'N/A')}
📏 <b>Spread:</b> {spread:.2f} ({spread_pct:.3f}%)

📈 <b>24h High:</b> {high}
📉 <b>24h Low:</b> {low}
📊 <b>Change:</b> {pc} ({pcp})

🕐 <b>Time:</b> {time_str}"""
        
        return message
    except Exception as e:
        return f"XAUUSD Price: {data.get('ask', 'N/A')} (Error formatting: {e})"

# Check price alerts
def check_price_alerts(current_price_value):
    """Check if any alerts should be triggered"""
    global price_alerts
    triggered_alerts = []
    
    for chat_id, alerts in list(price_alerts.items()):
        for alert in alerts[:]:  # Copy list to avoid modification during iteration
            alert_price = alert['price']
            direction = alert['direction']
            alert_id = alert['id']
            
            triggered = False
            if direction == 'above' and current_price_value >= alert_price:
                triggered = True
                message = f"🔔 <b>Alert Triggered!</b>\n\nPrice reached {current_price_value:.2f} (above {alert_price:.2f})"
            elif direction == 'below' and current_price_value <= alert_price:
                triggered = True
                message = f"🔔 <b>Alert Triggered!</b>\n\nPrice reached {current_price_value:.2f} (below {alert_price:.2f})"
            
            if triggered:
                safe_send_message(chat_id, message, parse_mode='HTML')
                alerts.remove(alert)
                triggered_alerts.append((chat_id, alert_id))
    
    # Remove empty alert lists
    price_alerts = {k: v for k, v in price_alerts.items() if v}

# Function to manage WebSocket connection
def manage_websocket():
    global ws, current_price
    while True:
        try:
            if not ws or not ws.sock or not ws.sock.connected:
                current_price = "Attempting to reconnect..."
                notify_users("WebSocket disconnected. Attempting to reconnect...")
                start_websocket()
            time.sleep(10)  # Check connection status every 10 seconds
        except Exception as e:
            current_price = "Error: " + str(e)
            notify_users("Error in WebSocket connection: " + str(e))
            time.sleep(10)

# WebSocket Functions
def send_websocket_request(data):
    global ws
    if ws and ws.sock and ws.sock.connected:
        ws.send(data)

def on_message(ws, message):
    global current_price, current_price_data
    if str(message) == "o":
        send_websocket_request(start_gold_message)
    if "timestamp" in message:
        """a["{\"message\":\"pid-68::{\\\"pid\\\":\\\"68\\\",\\\"last_dir\\\":\\\"greenBg\\\",\\\"last_numeric\\\":5163.06,\\\"last\\\":\\\"5,163.06\\\",\\\"bid\\\":\\\"5,162.43\\\",\\\"ask\\\":\\\"5,163.69\\\",\\\"high\\\":\\\"5,190.29\\\",\\\"low\\\":\\\"5,084.15\\\",\\\"last_close\\\":\\\"5,088.65\\\",\\\"pc\\\":\\\"+74.41\\\",\\\"pcp\\\":\\\"+1.46%\\\",\\\"pc_col\\\":\\\"greenFont\\\",\\\"time\\\":\\\"02:54:37\\\",\\\"timestamp\\\":1772592877}\"}"]"""
        try:
            # Simple extraction method - find JSON object after '::'
            # Works for both formats: a["..."] and direct format
            data = message.replace("\\", "")
            start = data.find('{', data.find('::'))
            end = data.rfind('}')
            if start != -1 and end != -1 and end > start:
                data_str = data[start:end+1]

                """
                data_str = {"pid":"68","last_dir":"redBg","last_numeric":5158.29,"last":"5,158.29","bid":"5,157.66","ask":"5,158.92","high":"5,190.29","low":"5,084.15","last_close":"5,088.65","pc":"+69.64","pcp":"+1.37%","pc_col":"greenFont","time":"03:02:05","timestamp":1772593325}"}
                """
                data_str = data_str[:-2]
                data = json.loads(data_str)
                # Store full price data
                current_price_data = data
                current_price = format_price_message(data)
                
                # Check alerts
                check_price_alerts(float(data['last_numeric']))
        except Exception as e:
            print(f"Error parsing message: {e}")
            print(f"Message preview: {str(message)[:200]}")

def on_error(ws, error):
    global current_price
    print("WebSocket error:", error)
    current_price = "WebSocket error: " + str(error)
    notify_users("WebSocket error: " + str(error))

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed:", close_status_code, close_msg)

# Start WebSocket Connection
def start_websocket():
    global ws
    wss_url = "wss://streaming.forexpros.com/echo/687/om5dyu2r/websocket"
    ws = websocket.WebSocketApp(wss_url, on_message=on_message, on_error=on_error, on_close=on_close)
    ws_thread = threading.Thread(target=ws.run_forever)
    ws_thread.start()

# Telegram Bot Functions
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = create_main_menu()
    help_text = """Welcome to the XAUUSD Gold Price Bot! 🪙

<b>Quick Actions:</b>
• Get instant price updates
• Set price alerts
• Customize update frequency
• Manage your preferences

Use the menu buttons below or type /help for all commands."""
    safe_send_message(message.chat.id, help_text, reply_markup=markup, parse_mode='HTML')

def create_main_menu():
    """Create the main menu keyboard"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_price = types.InlineKeyboardButton('💰 Current Price', callback_data='get_price')
    btn_updates = types.InlineKeyboardButton('▶️ Start Updates', callback_data='start_updates')
    btn_alerts = types.InlineKeyboardButton('🔔 Alerts', callback_data='alerts_menu')
    btn_settings = types.InlineKeyboardButton('⚙️ Settings', callback_data='settings')
    btn_help = types.InlineKeyboardButton('❓ Help', callback_data='help_menu')
    markup.add(btn_price, btn_updates)
    markup.add(btn_alerts, btn_settings)
    markup.add(btn_help)
    return markup

def create_alerts_menu():
    """Create alerts management menu"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_list = types.InlineKeyboardButton('📋 My Alerts', callback_data='list_alerts')
    btn_add = types.InlineKeyboardButton('➕ Add Alert', callback_data='add_alert')
    btn_delete = types.InlineKeyboardButton('🗑️ Delete Alert', callback_data='delete_alert_menu')
    btn_back = types.InlineKeyboardButton('🔙 Back', callback_data='back_menu')
    markup.add(btn_list, btn_add)
    markup.add(btn_delete)
    markup.add(btn_back)
    return markup

def create_settings_menu():
    """Create settings menu"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_freq = types.InlineKeyboardButton('⏱️ Frequency', callback_data='frequency_menu')
    btn_status = types.InlineKeyboardButton('📊 Status', callback_data='user_status')
    btn_stop = types.InlineKeyboardButton('⏸️ Stop Updates', callback_data='end')
    btn_back = types.InlineKeyboardButton('🔙 Back', callback_data='back_menu')
    markup.add(btn_freq, btn_status)
    markup.add(btn_stop)
    markup.add(btn_back)
    return markup

def create_frequency_menu():
    """Create frequency selection menu"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_10 = types.InlineKeyboardButton('⚡ 10s', callback_data='set_freq_10')
    btn_30 = types.InlineKeyboardButton('⚡ 30s', callback_data='set_freq_30')
    btn_60 = types.InlineKeyboardButton('⏱️ 60s', callback_data='set_freq_60')
    btn_120 = types.InlineKeyboardButton('🕐 120s', callback_data='set_freq_120')
    btn_300 = types.InlineKeyboardButton('🕐 300s', callback_data='set_freq_300')
    btn_custom = types.InlineKeyboardButton('✏️ Custom', callback_data='set_freq_custom')
    btn_back = types.InlineKeyboardButton('🔙 Back', callback_data='settings')
    markup.add(btn_10, btn_30)
    markup.add(btn_60, btn_120)
    markup.add(btn_300, btn_custom)
    markup.add(btn_back)
    return markup

@bot.message_handler(commands=['price'])
def get_price_command(message):
    """One-time price check command"""
    if current_price_data:
        safe_send_message(message.chat.id, current_price, parse_mode='HTML')
    else:
        safe_send_message(message.chat.id, "⏳ Waiting for price data... Please try again in a moment.")

@bot.message_handler(commands=['alert'])
def set_alert(message):
    """Set a price alert"""
    try:
        args = message.text.split()
        if len(args) < 2:
            safe_send_message(message.chat.id, "❌ Usage: /alert &lt;price&gt;\nExample: /alert 5400", parse_mode='HTML')
            return
        
        target_price = float(args[1])
        chat_id = int(message.chat.id)  # Ensure it's an integer
        
        # Determine direction based on current price
        if current_price_data and isinstance(current_price_data, dict):
            try:
                last_numeric = current_price_data.get('last_numeric', 0)
                # Handle both string and numeric values
                if isinstance(last_numeric, str):
                    # Remove commas and convert
                    last_numeric = float(last_numeric.replace(',', ''))
                else:
                    last_numeric = float(last_numeric)
                current_price_value = last_numeric
                direction = 'above' if target_price > current_price_value else 'below'
            except (ValueError, TypeError) as e:
                print(f"Error parsing current price: {e}, data: {current_price_data}")
                direction = 'above'  # Default to above if can't parse
        else:
            direction = 'above'  # Default to above if no current price
        
        global alert_counter
        alert_counter += 1
        alert_id = alert_counter
        
        # Ensure the list exists for this chat_id
        if chat_id not in price_alerts:
            price_alerts[chat_id] = []
        
        price_alerts[chat_id].append({
            'price': target_price,
            'direction': direction,
            'id': alert_id
        })
        
        safe_send_message(message.chat.id, 
                         f"✅ Alert #{alert_id} set!\n\n"
                         f"Price: {target_price:.2f}\n"
                         f"Direction: {direction.upper()}\n\n"
                         f"You'll be notified when price reaches this level.",
                         parse_mode='HTML')
    except ValueError as e:
        safe_send_message(message.chat.id, f"❌ Invalid price. Please enter a number.\nExample: /alert 5400\n\nError: {str(e)}")
    except Exception as e:
        error_msg = str(e)
        print(f"Error setting alert for user {message.chat.id}: {error_msg}")
        print(f"Exception type: {type(e).__name__}")
        safe_send_message(message.chat.id, f"❌ Error setting alert: {error_msg}\n\nPlease try again or contact support.")

@bot.message_handler(commands=['alerts'])
def list_alerts(message):
    """List user's active alerts"""
    chat_id = message.chat.id
    alerts = price_alerts.get(chat_id, [])
    
    if not alerts:
        safe_send_message(message.chat.id, "📭 You have no active alerts.\n\nUse /alert &lt;price&gt; to set one.", parse_mode='HTML')
        return
    
    alert_list = "🔔 <b>Your Active Alerts:</b>\n\n"
    for alert in alerts:
        alert_list += f"#{alert['id']} - {alert['direction'].upper()} {alert['price']:.2f}\n"
    
    alert_list += "\nUse /delete_alert &lt;id&gt; to remove an alert."
    safe_send_message(message.chat.id, alert_list, parse_mode='HTML')

@bot.message_handler(commands=['delete_alert'])
def delete_alert(message):
    """Delete a price alert"""
    try:
        args = message.text.split()
        if len(args) < 2:
            safe_send_message(message.chat.id, "❌ Usage: /delete_alert &lt;id&gt;\nExample: /delete_alert 1", parse_mode='HTML')
            return
        
        alert_id = int(args[1])
        chat_id = message.chat.id
        alerts = price_alerts.get(chat_id, [])
        
        # Find and remove alert
        removed = False
        for alert in alerts[:]:
            if alert['id'] == alert_id:
                alerts.remove(alert)
                removed = True
                break
        
        if removed:
            safe_send_message(message.chat.id, f"✅ Alert #{alert_id} deleted.")
        else:
            safe_send_message(message.chat.id, f"❌ Alert #{alert_id} not found.")
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid alert ID. Please enter a number.")
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Error deleting alert: {e}")

@bot.message_handler(commands=['frequency'])
def set_frequency(message):
    """Set update frequency for user"""
    try:
        args = message.text.split()
        if len(args) < 2:
            current_freq = user_frequencies[message.chat.id]
            safe_send_message(message.chat.id, 
                            f"📊 Your current update frequency: {current_freq} seconds\n\n"
                            f"Usage: /frequency &lt;seconds&gt;\n"
                            f"Example: /frequency 30\n"
                            f"Minimum: 10 seconds", 
                            parse_mode='HTML')
            return
        
        frequency = int(args[1])
        if frequency < 10:
            safe_send_message(message.chat.id, "❌ Minimum frequency is 10 seconds.")
            return
        
        user_frequencies[message.chat.id] = frequency
        safe_send_message(message.chat.id, f"✅ Update frequency set to {frequency} seconds.")
    except ValueError:
        safe_send_message(message.chat.id, "❌ Invalid frequency. Please enter a number (minimum 10).")
    except Exception as e:
        safe_send_message(message.chat.id, f"❌ Error setting frequency: {e}")

@bot.message_handler(commands=['stop'])
def stop_updates(message):
    """Stop receiving price updates"""
    chat_id = message.chat.id
    if chat_id in active_users:
        del active_users[chat_id]
        safe_send_message(chat_id, "⏸️ Stopped sending price updates.")
    else:
        safe_send_message(chat_id, "ℹ️ You're not receiving updates. Use /start to begin.")

@bot.message_handler(commands=['help'])
def help_command(message):
    help_text = """📖 <b>XAUUSD Gold Price Bot - Commands</b>

<b>Price Commands:</b>
/price - Get current price instantly

<b>Update Commands:</b>
/start - Start receiving price updates
/stop - Stop receiving updates

<b>Alert Commands:</b>
/alert &lt;price&gt; - Set price alert
  Example: /alert 5400
/alerts - List all your active alerts
/delete_alert &lt;id&gt; - Delete an alert
  Example: /delete_alert 1

<b>Settings Commands:</b>
/frequency &lt;seconds&gt; - Set update frequency
  Example: /frequency 30
  Minimum: 10 seconds

<b>Other:</b>
/help - Show this help message

💡 <b>Tip:</b> Use the menu buttons for quick access to all features!"""
    safe_send_message(message.chat.id, help_text, parse_mode='HTML')

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    
    # Main menu actions
    if call.data == 'get_price':
        bot.answer_callback_query(call.id, "Getting current price...")
        if current_price_data:
            safe_send_message(chat_id, current_price, parse_mode='HTML')
        else:
            safe_send_message(chat_id, "⏳ Waiting for price data... Please try again in a moment.")
    
    elif call.data == 'start_updates':
        active_users[chat_id] = True
        frequency = user_frequencies[chat_id]
        bot.answer_callback_query(call.id, "Started receiving updates")
        safe_send_message(chat_id, f"✅ Started sending XAUUSD price updates!\n\n📊 Update frequency: {frequency} seconds\n\nUse Settings to change frequency.")
    
    elif call.data == 'end':
        if chat_id in active_users:
            del active_users[chat_id]
        bot.answer_callback_query(call.id, "Stopped updates")
        safe_send_message(chat_id, "⏸️ Stopped sending price updates.")
    
    # Alerts menu
    elif call.data == 'alerts_menu':
        bot.answer_callback_query(call.id, "Alerts menu")
        markup = create_alerts_menu()
        alerts = price_alerts.get(chat_id, [])
        alert_count = len(alerts)
        text = f"🔔 <b>Price Alerts</b>\n\nActive alerts: {alert_count}\n\nSelect an option:"
        safe_send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')
    
    elif call.data == 'list_alerts':
        alerts = price_alerts.get(chat_id, [])
        if not alerts:
            bot.answer_callback_query(call.id, "No active alerts")
            safe_send_message(chat_id, "📭 You have no active alerts.\n\nUse /alert &lt;price&gt; to set one.\nExample: /alert 5400", parse_mode='HTML')
        else:
            alert_list = "🔔 <b>Your Active Alerts:</b>\n\n"
            for alert in alerts:
                alert_list += f"#{alert['id']} - {alert['direction'].upper()} {alert['price']:.2f}\n"
            alert_list += "\nUse /delete_alert &lt;id&gt; to remove an alert."
            bot.answer_callback_query(call.id, f"Showing {len(alerts)} alerts")
            safe_send_message(chat_id, alert_list, parse_mode='HTML')
    
    elif call.data == 'add_alert':
        bot.answer_callback_query(call.id, "Use /alert command")
        safe_send_message(chat_id, "To add an alert, use:\n\n<code>/alert &lt;price&gt;</code>\n\nExample: <code>/alert 5400</code>\n\nThis will notify you when the price reaches the specified level.", parse_mode='HTML')
    
    elif call.data == 'delete_alert_menu':
        alerts = price_alerts.get(chat_id, [])
        if not alerts:
            bot.answer_callback_query(call.id, "No alerts to delete")
            safe_send_message(chat_id, "📭 You have no active alerts to delete.")
        else:
            bot.answer_callback_query(call.id, "Use /delete_alert command")
            alert_list = "🗑️ <b>Delete Alert</b>\n\nYour alerts:\n"
            for alert in alerts:
                alert_list += f"#{alert['id']} - {alert['direction'].upper()} {alert['price']:.2f}\n"
            alert_list += "\nUse: <code>/delete_alert &lt;id&gt;</code>\nExample: <code>/delete_alert 1</code>"
            safe_send_message(chat_id, alert_list, parse_mode='HTML')
    
    # Settings menu
    elif call.data == 'settings':
        frequency = user_frequencies[chat_id]
        is_active = chat_id in active_users
        markup = create_settings_menu()
        status_text = "✅ Active" if is_active else "⏸️ Inactive"
        text = f"⚙️ <b>Settings</b>\n\n📊 Status: {status_text}\n⏱️ Update frequency: {frequency} seconds\n\nSelect an option:"
        bot.answer_callback_query(call.id, "Settings")
        safe_send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')
    
    elif call.data == 'frequency_menu':
        current_freq = user_frequencies[chat_id]
        markup = create_frequency_menu()
        bot.answer_callback_query(call.id, "Frequency menu")
        safe_send_message(chat_id, f"⏱️ <b>Update Frequency</b>\n\nCurrent: {current_freq} seconds\n\nSelect a frequency:", reply_markup=markup, parse_mode='HTML')
    
    elif call.data.startswith('set_freq_'):
        if call.data == 'set_freq_custom':
            bot.answer_callback_query(call.id, "Use /frequency command")
            safe_send_message(chat_id, "To set a custom frequency, use:\n\n<code>/frequency &lt;seconds&gt;</code>\n\nExample: <code>/frequency 45</code>\n\nMinimum: 10 seconds", parse_mode='HTML')
        else:
            freq = int(call.data.split('_')[-1])
            user_frequencies[chat_id] = freq
            bot.answer_callback_query(call.id, f"Frequency set to {freq}s")
            safe_send_message(chat_id, f"✅ Update frequency set to {freq} seconds.")
    
    elif call.data == 'user_status':
        is_active = chat_id in active_users
        frequency = user_frequencies[chat_id]
        alerts_count = len(price_alerts.get(chat_id, []))
        status_text = "✅ Active" if is_active else "⏸️ Inactive"
        text = f"📊 <b>Your Status</b>\n\n"
        text += f"Updates: {status_text}\n"
        text += f"Frequency: {frequency} seconds\n"
        text += f"Active alerts: {alerts_count}\n"
        if current_price_data:
            current_val = float(current_price_data.get('last_numeric', 0))
            text += f"\n💰 Current price: {current_val:.2f}"
        bot.answer_callback_query(call.id, "Status")
        safe_send_message(chat_id, text, parse_mode='HTML')
    
    # Help menu
    elif call.data == 'help_menu':
        bot.answer_callback_query(call.id, "Help")
        help_command(type('obj', (object,), {'chat': type('obj', (object,), {'id': chat_id})()}))
    
    # Navigation
    elif call.data == 'back_menu':
        bot.answer_callback_query(call.id, "Main menu")
        markup = create_main_menu()
        help_text = """Welcome to the XAUUSD Gold Price Bot! 🪙

<b>Quick Actions:</b>
• Get instant price updates
• Set price alerts
• Customize update frequency
• Manage your preferences

Use the menu buttons below or type /help for all commands."""
        safe_send_message(chat_id, help_text, reply_markup=markup, parse_mode='HTML')

# Function to send price updates with exception handling
def send_price_updates():
    user_last_update = {}  # Track last update time per user
    while True:
        current_time = time.time()
        for chat_id in list(active_users.keys()):
            try:
                # Check if it's time to send update for this user
                frequency = user_frequencies[chat_id]
                last_update = user_last_update.get(chat_id, 0)
                
                if current_time - last_update >= frequency:
                    safe_send_message(chat_id, current_price, parse_mode='HTML')
                    user_last_update[chat_id] = current_time
            except Exception as e:
                print(f"Error sending message to {chat_id}: {e}")
        time.sleep(5)  # Check every 5 seconds

# Function to notify users about connection status with exception handling
def notify_users(message):
    for chat_id in list(active_users.keys()):
        try:
            safe_send_message(chat_id, message)
        except Exception as e:
            print(f"Error notifying user {chat_id}: {e}")

# Safe message sending function with Telegram API exception handling
def safe_send_message(chat_id, text, reply_markup=None, parse_mode=None):
    try:
        bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    except ApiException as e:
        print(f"Telegram API exception for chat {chat_id}: {e}")
    except Exception as e:
        print(f"General exception for chat {chat_id}: {e}")

# Start WebSocket Management Thread
websocket_management_thread = threading.Thread(target=manage_websocket)
websocket_management_thread.start()

# Start Price Update Thread
update_thread = threading.Thread(target=send_price_updates)
update_thread.start()

# Telegram Bot Polling
def bot_polling():
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            bot.stop_polling()
            print(f"Bot polling error: {e}")
            time.sleep(10)

# Start Bot Polling in a Separate Thread
bot_polling_thread = threading.Thread(target=bot_polling)
bot_polling_thread.start()
