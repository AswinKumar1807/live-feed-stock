import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_mysqldb import MySQL
from flask_bcrypt import Bcrypt
import MySQLdb.cursors
import re
import pandas as pd
import io
from flask import send_file


app = Flask(__name__)
app.config.from_object('config.Config')

mysql = MySQL(app)
bcrypt = Bcrypt(app)

def init_db():
    with app.app_context():
        cursor = mysql.connection.cursor()

         # Existing tables
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                mobile VARCHAR(15) NOT NULL,
                password VARCHAR(100) NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sites (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT,
                name VARCHAR(100) NOT NULL,
                location VARCHAR(100) NOT NULL,
                supervisor_name VARCHAR(100) NOT NULL,
                supervisor_contact VARCHAR(15) NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ponds (
                id INT AUTO_INCREMENT PRIMARY KEY,
                site_id INT,
                area FLOAT NOT NULL,
                prawn_count INT NOT NULL,
                feed_per_day FLOAT NOT NULL,
                feed_increase_per_day FLOAT NOT NULL,
                accumulated_feed FLOAT NOT NULL,
                feed_code VARCHAR(100) NOT NULL,
                creation_date DATE NOT NULL,
                current_day INT NOT NULL,
                harvested_finish BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (site_id) REFERENCES sites(id)
            )
        ''')

        # Updated feed tracking related to sites and ponds
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS site_feed (
                id INT AUTO_INCREMENT PRIMARY KEY,
                site_id INT,
                feed_code VARCHAR(100) NOT NULL,
                quantity_in_kg FLOAT NOT NULL,
                FOREIGN KEY (site_id) REFERENCES sites(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pond_feed (
                id INT AUTO_INCREMENT PRIMARY KEY,
                pond_id INT,
                feed_code VARCHAR(100) NOT NULL,
                quantity_in_kg FLOAT NOT NULL,
                FOREIGN KEY (pond_id) REFERENCES ponds(id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS currentdate (
                id INT AUTO_INCREMENT PRIMARY KEY,
                current_day INT NOT NULL,
                currentdate DATE NOT NULL
            )
        ''')

        # Check if the current date entry exists
        cursor.execute('SELECT * FROM currentdate')
        row = cursor.fetchone()
        if not row:
            # Insert initial current date
            current_day = 1  # Start with day 1
            currentdate = datetime.datetime.now().date()
            cursor.execute('INSERT INTO currentdate (current_day, currentdate) VALUES (%s, %s)', (current_day, currentdate))

        mysql.connection.commit()
        cursor.close()
# Initialize the database
init_db()

df = pd.read_excel('static/feed.xlsx')
def parse_range(value):
    if isinstance(value, str) and '-' in value:
        parts = value.split('-')
        return sum(float(part) for part in parts) / len(parts)
    else:
        return float(value)

# Create list of dictionaries
feed_data = []
for index, row in df.iterrows():
    feed_data.append({
        "day": int(row['day']),
        "feed_per_day": parse_range(row['feed_per_day']),
        "feed_increase_per_day": parse_range(row['feed_increase_per_day']),
        "accumulated_feed": parse_range(row['accumulated_feed']),
        "feed_code": str(row['feed_code'])
    })

@app.route('/edit_pond/<int:pond_id>', methods=['GET', 'POST'])
def edit_pond(pond_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    if request.method == 'POST':
        # Fetch updated details from the form
        new_prawn_count = int(request.form['prawn_count'])

        # Get current details of the pond to keep creation date and current day unchanged
        cursor.execute('SELECT prawn_count, accumulated_feed, current_day FROM ponds WHERE id = %s', (pond_id,))
        pond = cursor.fetchone()

        if not pond:
            flash('Pond not found!', 'danger')
            return redirect(url_for('summary'))

        current_prawn_count = pond['prawn_count']
        current_accumulated_feed = pond['accumulated_feed']
        current_day = pond['current_day']

        # Update pond details in the database without changing creation date or current day
        cursor.execute('UPDATE ponds SET prawn_count = %s WHERE id = %s',
                       (new_prawn_count, pond_id))

        # Recalculate accumulated feed
        new_accumulated_feed = calculate_accumulated_feed(new_prawn_count, current_day)

        # Update accumulated feed in the database
        cursor.execute('UPDATE ponds SET accumulated_feed = %s WHERE id = %s',
                       (new_accumulated_feed, pond_id))

        mysql.connection.commit()

        cursor.close()
        flash('Pond updated successfully!', 'success')
        return redirect(url_for('summary'))

    # Fetch existing pond details
    cursor.execute('SELECT * FROM ponds WHERE id = %s', (pond_id,))
    pond = cursor.fetchone()

    cursor.close()
    return render_template('edit_pond.html', pond=pond)


def calculate_feed_details(prawn_count, day):
    if day > 120:
        return {
            "feed_per_day": 0,
            "feed_increase_per_day": 0,
            "accumulated_feed": 0,
            "feed_code": "feeds cultivated"
        }

    feed_info = next((item for item in feed_data if item["day"] == day), None)
    if not feed_info:
        return None

    scale_factor = prawn_count / 100000.0
    scaled_feed_details = {
        "feed_per_day": feed_info["feed_per_day"] * scale_factor,
        "feed_increase_per_day": feed_info["feed_increase_per_day"],
        "accumulated_feed": feed_info["accumulated_feed"] * scale_factor,
        "feed_code": feed_info["feed_code"]
    }

    return scaled_feed_details

def calculate_accumulated_feed(prawn_count, current_day):
    total_accumulated_feed = 0
    for day in range(1, current_day + 1):
        feed_details = calculate_feed_details(prawn_count, day)
        if feed_details:
            total_accumulated_feed += feed_details["feed_per_day"]
    return total_accumulated_feed

@app.route('/download_feed_sheet')
def download_feed_sheet():
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Fetch site and pond details
    cursor.execute('''
        SELECT sites.id as site_id, sites.name as site_name, sites.location, ponds.id as pond_id, ponds.area, ponds.prawn_count, ponds.creation_date
        FROM sites
        JOIN ponds ON sites.id = ponds.site_id
        WHERE sites.user_id = %s
    ''', (session['id'],))
    ponds = cursor.fetchall()

    cursor.close()

    feed_sheets = []

    for pond in ponds:
        pond_feed_data = []

        for day in range(1, 121):
            feed_details = calculate_feed_details(pond['prawn_count'], day)
            if feed_details is None:
                feed_details = {
                    'feed_per_day': 'N/A',
                    'feed_increase_per_day': 'N/A',
                    'accumulated_feed': 'N/A',
                    'feed_code': 'N/A'
                }

            # Calculate the additional columns
            feed_per_day_bag = (feed_details['feed_per_day'] / 25) if feed_details['feed_per_day'] != 'N/A' else 'N/A'
            feed_increase_per_day_bag = (feed_details['feed_increase_per_day'] / 25) if feed_details['feed_increase_per_day'] != 'N/A' else 'N/A'
            accumulated_feed_bag = (feed_details['accumulated_feed'] / 25) if feed_details['accumulated_feed'] != 'N/A' else 'N/A'

            feed_per_day_metric = (feed_details['feed_per_day'] / 100) if feed_details['feed_per_day'] != 'N/A' else 'N/A'
            feed_increase_per_day_metric = (feed_details['feed_increase_per_day'] / 100) if feed_details['feed_increase_per_day'] != 'N/A' else 'N/A'
            accumulated_feed_metric = (feed_details['accumulated_feed'] / 100) if feed_details['accumulated_feed'] != 'N/A' else 'N/A'

            pond_feed_data.append({
                'Day': day,
                'Feed Per Day (kg)': feed_details['feed_per_day'],
                'Feed Increase Per Day (kg)': feed_details['feed_increase_per_day'],
                'Accumulated Feed (kg)': feed_details['accumulated_feed'],
                'Feed Per Day (in bag)': feed_per_day_bag,
                'Feed Increase Per Day (in bag)': feed_increase_per_day_bag,
                'Accumulated Feed (in bag)': accumulated_feed_bag,
                'Feed Per Day (in metric)': feed_per_day_metric,
                'Feed Increase Per Day (in metric)': feed_increase_per_day_metric,
                'Accumulated Feed (in metric)': accumulated_feed_metric,
                'Feed Code': feed_details['feed_code']
            })

        pond_df = pd.DataFrame(pond_feed_data)
        feed_sheets.append({
            'site_name': pond['site_name'],
            'pond_id': pond['pond_id'],
            'pond_data': pond_df
        })

    # Create a Pandas Excel writer using an in-memory buffer
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        for sheet in feed_sheets:
            sheet_name = f'{sheet["site_name"]}-{sheet["pond_id"]}'
            sheet['pond_data'].to_excel(writer, sheet_name=sheet_name, index=False)

    buffer.seek(0)

    return send_file(buffer, as_attachment=True, download_name='feed_sheet.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if ((username == 'Admin') & (password == 'Admin@123')):
            session['loggedin'] = True
            return redirect(url_for('admin'))
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
        user = cursor.fetchone()
        cursor.close()

        if user and bcrypt.check_password_hash(user['password'], password):
            session['loggedin'] = True
            session['id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('summary'))
        else:
            flash('Invalid username or password! Please contact Admin for recovery', 'danger')
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        mobile = request.form['mobile']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        cursor = mysql.connection.cursor()
        cursor.execute('INSERT INTO users (username, mobile, password) VALUES (%s, %s, %s)', (username, mobile, password))
        mysql.connection.commit()
        cursor.close()
        flash('You have successfully registered! Please login.', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/signupadmin', methods=['GET', 'POST'])
def signupadmin():
    if request.method == 'POST':
        username = request.form['username']
        mobile = request.form['mobile']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        cursor = mysql.connection.cursor()
        cursor.execute('INSERT INTO users (username, mobile, password) VALUES (%s, %s, %s)', (username, mobile, password))
        mysql.connection.commit()
        cursor.close()
        flash('You have successfully registered! Please login.', 'success')
        return redirect(url_for('admin'))
    return render_template('signup.html')

@app.route('/logout')
def logout():
    session.pop('loggedin', None)
    session.pop('id', None)
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        num_sites = int(request.form['num_sites'])
        supervisor_name = request.form['supervisor_name']
        supervisor_contact = request.form['supervisor_contact']
        session['num_sites'] = num_sites
        session['supervisor_name'] = supervisor_name
        session['supervisor_contact'] = supervisor_contact
        return redirect(url_for('site_info', site_num=1))

    return render_template('profile.html')


@app.route('/site_info/<int:site_num>', methods=['GET', 'POST'])
def site_info(site_num):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        site_name = request.form['site_name']
        location = request.form['location']
        num_ponds = int(request.form['num_ponds'])
        supervisor_name = session.get('supervisor_name')
        supervisor_contact = session.get('supervisor_contact')
        cursor = mysql.connection.cursor()
        cursor.execute('''
            INSERT INTO sites (user_id, name, location, supervisor_name, supervisor_contact)
            VALUES (%s, %s, %s, %s, %s)
        ''', (session['id'], site_name, location, supervisor_name, supervisor_contact))
        site_id = cursor.lastrowid
        mysql.connection.commit()
        session[f'site_id_{site_num}'] = site_id
        session[f'num_ponds_{site_num}'] = num_ponds
        return redirect(url_for('pond_info', site_num=site_num, pond_num=1))
    return render_template('site_info.html', site_num=site_num)


@app.route('/pond_info/<int:site_num>/<int:pond_num>', methods=['GET', 'POST'])
def pond_info(site_num, pond_num):
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    if request.method == 'POST':
        area = float(request.form['area'])
        prawn_count = int(request.form['prawn_count'])
        site_id = session[f'site_id_{site_num}']

        # Use the current date as the creation date for the new pond
        creation_date = datetime.datetime.now().date()

        # Calculate the current day for this pond (will always be 1 upon creation)
        current_day = 1

        # Insert basic pond data into the database
        cursor.execute('''
            INSERT INTO ponds (site_id, area, prawn_count, creation_date, current_day)
            VALUES (%s, %s, %s, %s, %s)
        ''', (site_id, area, prawn_count, creation_date, current_day))
        mysql.connection.commit()

        cursor.close()

        if pond_num < session[f'num_ponds_{site_num}']:
            return redirect(url_for('pond_info', site_num=site_num, pond_num=pond_num + 1))
        elif site_num < session['num_sites']:
            return redirect(url_for('site_info', site_num=site_num + 1))
        else:
            return redirect(url_for('summary'))

    # Fetch pond details from the database
    cursor.execute('SELECT area, prawn_count, creation_date, current_day FROM ponds WHERE site_id = %s', (session[f'site_id_{site_num}'],))
    pond_details = cursor.fetchall()

    cursor.close()

    return render_template('pond_info.html',
                           site_num=site_num,
                           pond_num=pond_num,
                           pond_details=pond_details)

@app.route('/summary', methods=['GET', 'POST'])
def summary():
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    unit = request.form.get('unit', 'kg')  # Get the selected unit, default to 'kg'
    user_id = session['id']  # Ensure we have the user ID from session

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Fetch user details
    cursor.execute('SELECT username, mobile FROM users WHERE id = %s', (user_id,))
    user = cursor.fetchone()

    # Fetch overall summary
    cursor.execute('''
        SELECT COUNT(*) as total_ponds, SUM(ponds.area) as total_area, SUM(ponds.prawn_count) as total_prawn_count
        FROM ponds
        JOIN sites ON ponds.site_id = sites.id
        WHERE sites.user_id = %s
    ''', (user_id,))
    overall = cursor.fetchone()

    # Fetch site and pond details
    cursor.execute('''
        SELECT sites.id as site_id, sites.name, sites.location, sites.supervisor_name, sites.supervisor_contact,
               ponds.id as pond_id, ponds.area, ponds.prawn_count, ponds.creation_date, ponds.harvested_finish
        FROM sites
        LEFT JOIN ponds ON sites.id = ponds.site_id
        WHERE sites.user_id = %s
    ''', (user_id,))
    ponds = cursor.fetchall()

    # Fetch feed supplied data for the user
    cursor.execute('''
        SELECT feed_code, quantity_in_kg
        FROM feed_supplied
        WHERE user_id = %s
    ''', (user_id,))
    feed_supplied = cursor.fetchall()

    cursor.execute('''
        SELECT feed_code, SUM(quantity_in_kg) as total_supplied
        FROM feed_supplied
        WHERE user_id = %s
        GROUP BY feed_code
    ''', (user_id,))
    feed_supplied_total = cursor.fetchall()

    # Fetch consumed quantity data for each site and feed code
    cursor.execute('''
        SELECT feed_code, SUM(quantity_in_kg) as total_consumed
        FROM site_feed
        WHERE site_id IN (SELECT id FROM sites WHERE user_id = %s)
        GROUP BY feed_code
    ''', (user_id,))
    consumed_quantities = cursor.fetchall()
    consumed_quantities_dict = {item['feed_code']: item['total_consumed'] for item in consumed_quantities}
    cursor.close()

    # Process feed supplied and consumed data to determine leftovers
    total_supplied = {}
    total_consumed = {}

    # Populate total supplied
    for item in feed_supplied_total:
        feed_code = item['feed_code']
        total_supplied[feed_code] = item['total_supplied']

    # Populate total consumed
    for item in consumed_quantities:
        feed_code = item['feed_code']
        if feed_code not in total_consumed:
            total_consumed[feed_code] = 0
        total_consumed[feed_code] += item['total_consumed']

    # Calculate leftover stock
    leftover_stock = {}
    for feed_code in total_supplied:
        supplied = total_supplied.get(feed_code, 0)
        consumed = total_consumed.get(feed_code, 0)
        leftover_stock[feed_code] = supplied - consumed

    site_data = {}
    site_feed_summary = {}
    today_date = datetime.datetime.now().date()

    def convert_feed_units(value, unit):
        if isinstance(value, (int, float)):
            if unit == 'bag':
                return value / 25
            elif unit == 'metric':
                return value / 100
        return value

    for pond in ponds:
        site_id = pond['site_id']
        if site_id not in site_data:
            site_data[site_id] = {
                'id': site_id,
                'name': pond['name'],
                'location': pond['location'],
                'supervisor_name': pond['supervisor_name'],
                'supervisor_contact': pond['supervisor_contact'],
                'total_area': 0,
                'total_prawn_count': 0,
                'ponds': []
            }
            site_feed_summary[site_id] = {}

        site_data[site_id]['total_area'] += pond['area']
        site_data[site_id]['total_prawn_count'] += pond['prawn_count']

        if pond['creation_date']:
            pond_creation_date = pond['creation_date']
            current_day = (today_date - pond_creation_date).days + 1
            feed_details = calculate_feed_details(pond['prawn_count'], current_day)
            if feed_details is None:
                feed_details = {
                    'feed_per_day': 'N/A',
                    'feed_increase_per_day': 'N/A',
                    'accumulated_feed': 'N/A',
                    'feed_code': 'N/A'
                }
            pond.update(feed_details)
            pond['current_day'] = current_day

            # Check if feed history for today already exists
            cursor = mysql.connection.cursor()
            cursor.execute('''
                SELECT 1 FROM pond_feed_history WHERE pond_id = %s AND day_number = %s
            ''', (pond['pond_id'], current_day))
            existing_entry = cursor.fetchone()

            if not existing_entry:
                # Insert new feed history entry only if not already inserted today
                cursor.execute('''
                    INSERT INTO pond_feed_history (pond_id, day_number, feed_per_day, feed_increase_per_day, accumulated_feed, feed_code)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (pond['pond_id'], current_day, feed_details['feed_per_day'], feed_details['feed_increase_per_day'], feed_details['accumulated_feed'], feed_details['feed_code']))
                mysql.connection.commit()

            cursor.close()
        else:
            pond.update({
                'feed_per_day': 'N/A',
                'feed_increase_per_day': 'N/A',
                'accumulated_feed': 'N/A',
                'feed_code': 'N/A',
                'current_day': 'N/A'
            })

        pond['feed_per_day'] = convert_feed_units(pond['feed_per_day'], unit)
        pond['feed_increase_per_day'] = convert_feed_units(pond['feed_increase_per_day'], unit)
        pond['accumulated_feed'] = convert_feed_units(pond['accumulated_feed'], unit)

        feed_code = pond['feed_code']
        if feed_code not in site_feed_summary[site_id]:
            site_feed_summary[site_id][feed_code] = {
                'total_feed_per_day': 0,
                'total_accumulated_feed': 0
            }
        site_feed_summary[site_id][feed_code]['total_feed_per_day'] += pond['feed_per_day'] if isinstance(pond['feed_per_day'], (int, float)) else 0
        site_feed_summary[site_id][feed_code]['total_accumulated_feed'] += pond['accumulated_feed'] if isinstance(pond['accumulated_feed'], (int, float)) else 0

        pond['creation_date'] = pond['creation_date'].strftime('%Y-%m-%d') if pond['creation_date'] else 'N/A'
        pond['harvested_finish'] = pond.get('harvested_finish', False)
        site_data[site_id]['ponds'].append(pond)

    for site_id in site_feed_summary:
        for feed_code, summary in site_feed_summary[site_id].items():
            summary['total_feed_per_day'] = convert_feed_units(summary['total_feed_per_day'], unit)
            summary['total_accumulated_feed'] = convert_feed_units(summary['total_accumulated_feed'], unit)

    return render_template('summary.html',
                       username=user['username'],
                       mobile=user['mobile'],
                       total_ponds=overall['total_ponds'],
                       total_area=overall['total_area'],
                       total_prawn_count=overall['total_prawn_count'],
                       sites=list(site_data.values()),
                       site_feed_summary=site_feed_summary,
                       selected_unit=unit,
                       feed_supplied=feed_supplied,
                       feed_supplied_total=feed_supplied_total,
                       consumed_quantities=consumed_quantities_dict,  # Pass the dictionary
                       leftover_stock=leftover_stock)



@app.route('/show_history/<int:pond_id>', methods=['GET'])
def show_history(pond_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Fetch the history for the specified pond
    cursor.execute('''
        SELECT day_number, feed_per_day, feed_increase_per_day, accumulated_feed, feed_code
        FROM pond_feed_history
        WHERE pond_id = %s
        ORDER BY day_number ASC
    ''', (pond_id,))
    history = cursor.fetchall()

    cursor.execute('''
        SELECT ponds.id, ponds.area, ponds.prawn_count, ponds.creation_date
        FROM ponds
        WHERE id = %s
    ''', (pond_id,))
    pond_details = cursor.fetchone()
    
    cursor.close()

    # Render the history in a new template
    return render_template('pond_history.html',
                           pond=pond_details,
                           history=history)


@app.route('/harvested_finish/<int:pond_id>', methods=['POST'])
def harvested_finish(pond_id):
    if 'loggedin' not in session:
        return jsonify({'success': False, 'message': 'User not logged in'})

    cursor = mysql.connection.cursor()

    # Update the harvested_finish status for the pond
    try:
        cursor.execute('''
            UPDATE ponds
            SET harvested_finish = TRUE
            WHERE id = %s
        ''', (pond_id,))
        mysql.connection.commit()

        # Check if the update was successful
        if cursor.rowcount == 0:
            return jsonify({'success': False, 'message': 'Pond not found or already harvested'})

        return jsonify({'success': True, 'message': 'Pond status updated successfully'})
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({'success': False, 'message': 'Error updating pond status'})
    finally:
        cursor.close()


@app.route('/admin')
def admin():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Fetch total number of users
    cursor.execute('SELECT COUNT(*) as total_users FROM users')
    total_users = cursor.fetchone()['total_users']

    # Fetch total number of sites
    cursor.execute('SELECT COUNT(*) as total_sites FROM sites')
    total_sites = cursor.fetchone()['total_sites']

    # Fetch total number of ponds and total prawn count
    cursor.execute('SELECT COUNT(*) as total_ponds, SUM(prawn_count) as total_prawn_count FROM ponds')
    pond_summary = cursor.fetchone()
    total_ponds = pond_summary['total_ponds']
    total_prawn_count = pond_summary['total_prawn_count']

    # Fetch detailed feed summary per site
    cursor.execute('''
        SELECT sites.id as site_id, sites.name as site_name, COALESCE(SUM(ponds.feed_per_day), 0) as feed_per_day
        FROM sites
        LEFT JOIN ponds ON sites.id = ponds.site_id
        GROUP BY sites.id
    ''')
    feed_summary = cursor.fetchall()

    # Calculate the total feed required per day from the feed_summary
    total_feed_per_day = sum(site['feed_per_day'] for site in feed_summary)

    # Fetch user-specific details including total feed per day
    cursor.execute('''
        SELECT users.id, users.username, COUNT(sites.id) as site_count, COUNT(ponds.id) as pond_count,
               SUM(ponds.prawn_count) as total_prawn_count, COALESCE(SUM(ponds.feed_per_day), 0) as total_feed_per_day
        FROM users
        LEFT JOIN sites ON users.id = sites.user_id
        LEFT JOIN ponds ON sites.id = ponds.site_id
        GROUP BY users.id
    ''')
    user_details = cursor.fetchall()

    cursor.close()

    return render_template('admin_dashboard.html',
                           total_users=total_users,
                           total_sites=total_sites,
                           total_ponds=total_ponds,
                           total_prawn_count=total_prawn_count,
                           total_feed_per_day=total_feed_per_day,
                           feed_summary=feed_summary,
                           user_details=user_details)


@app.route('/admin/user/<int:user_id>', methods=['GET', 'POST'])
def user_details(user_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

    # Fetch user details
    cursor.execute('SELECT username FROM users WHERE id = %s', (user_id,))
    user = cursor.fetchone()

    # Fetch sites and ponds details for the user
    cursor.execute('''
        SELECT sites.id as site_id, sites.name as site_name, sites.location, sites.supervisor_name, sites.supervisor_contact,
               ponds.id as pond_id, ponds.area, ponds.prawn_count, ponds.creation_date
        FROM sites
        LEFT JOIN ponds ON sites.id = ponds.site_id
        WHERE sites.user_id = %s
    ''', (user_id,))
    ponds = cursor.fetchall()

    # Fetch feed supplied data for the user
    cursor.execute('''
        SELECT feed_code, quantity_in_kg
        FROM feed_supplied
        WHERE user_id = %s
    ''', (user_id,))
    feed_supplied = cursor.fetchall()

    cursor.execute('''
        SELECT feed_code, SUM(quantity_in_kg) as total_supplied
        FROM feed_supplied
        WHERE user_id = %s
        GROUP BY feed_code
    ''', (user_id,))
    feed_supplied_total = cursor.fetchall()

     # Fetch consumed quantity data for each site and feed code
    cursor.execute('''
        SELECT site_id, feed_code, SUM(quantity_in_kg) as total_consumed
        FROM site_feed
        GROUP BY site_id, feed_code
    ''')
    consumed_quantities = cursor.fetchall()  # Ensure this query runs correctly
    cursor.execute('''
        SELECT site_id, feed_code, SUM(quantity_in_kg) as total_consumed
        FROM site_feed
        WHERE site_id IN (SELECT id FROM sites WHERE user_id = %s)
        GROUP BY site_id, feed_code
    ''', (user_id,))
    consumed_quantities_total = cursor.fetchall()

    cursor.close()

    site_data = {}
    today_date = datetime.datetime.now().date()
    custom_days = request.form.get('custom_days', 7, type=int)

    for pond in ponds:
        site_id = pond['site_id']
        if site_id not in site_data:
            site_data[site_id] = {
                'id': site_id,
                'name': pond['site_name'],
                'location': pond['location'],
                'supervisor_name': pond['supervisor_name'],
                'supervisor_contact': pond['supervisor_contact'],
                'total_area': 0,
                'total_prawn_count': 0,
                'total_feed_per_day': 0,
                'ponds': [],
                'site_feed_summary': {},
                'consumed_quantities': {}
            }

        site_data[site_id]['total_area'] += pond['area']
        site_data[site_id]['total_prawn_count'] += pond['prawn_count']

        if pond['creation_date']:
            pond_creation_date = pond['creation_date']
            current_day = (today_date - pond_creation_date).days + 1
            feed_details = calculate_feed_details(pond['prawn_count'], current_day)
            if feed_details is None:
                feed_details = {
                    'feed_per_day': 0,
                    'feed_increase_per_day': 0,
                    'accumulated_feed': 0,
                    'feed_code': 'N/A'
                }
            pond.update(feed_details)
            pond['current_day'] = current_day
        else:
            pond.update({
                'feed_per_day': 0,
                'feed_increase_per_day': 0,
                'accumulated_feed': 0,
                'feed_code': 'N/A',
                'current_day': 0
            })

        for day in range(current_day, current_day + custom_days):
            feed_details = calculate_feed_details(pond['prawn_count'], day)
            feed_code = feed_details['feed_code']
            feed_per_day = feed_details['feed_per_day']

            if feed_code not in site_data[site_id]['site_feed_summary']:
                site_data[site_id]['site_feed_summary'][feed_code] = {
                    'total_feed_per_day': 0,
                    'total_accumulated_feed': 0,
                    'next_days_feed': 0
                }
            site_data[site_id]['site_feed_summary'][feed_code]['total_feed_per_day'] += feed_per_day
            site_data[site_id]['site_feed_summary'][feed_code]['total_accumulated_feed'] += feed_details['accumulated_feed']
            site_data[site_id]['site_feed_summary'][feed_code]['next_days_feed'] += feed_per_day

        pond['creation_date'] = pond['creation_date'].strftime('%Y-%m-%d') if pond['creation_date'] else 'N/A'
        site_data[site_id]['ponds'].append(pond)

    # Update consumed quantities in the site data
    for item in consumed_quantities:
        site_id = item['site_id']
        feed_code = item['feed_code']
        total_consumed = item['total_consumed']
        if site_id in site_data:
            site_data[site_id]['consumed_quantities'][feed_code] = total_consumed

    # Calculate feed supplied and consumed summary without grouping feed codes
    overall_leftover_feed = {}

    for feed_item in feed_supplied_total:
        feed_code = feed_item['feed_code']
        total_supplied = feed_item['total_supplied']

        total_consumed = sum(site['consumed_quantities'].get(feed_code, 0) for site in site_data.values())

        # Calculate leftover feed stock for each feed code
        overall_leftover_feed[feed_code] = total_supplied - total_consumed

    return render_template('user_details.html',
                           user=user,
                           sites=list(site_data.values()),
                           custom_days=custom_days,
                           feed_supplied=feed_supplied,
                           overall_leftover_feed=overall_leftover_feed)






@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)

   # Delete related pond_feed records
    cursor.execute('DELETE FROM pond_feed WHERE pond_id IN (SELECT id FROM ponds WHERE site_id IN (SELECT id FROM sites WHERE user_id = %s))', (user_id,))

    # Delete related site_feed records
    cursor.execute('DELETE FROM site_feed WHERE site_id IN (SELECT id FROM sites WHERE user_id = %s)', (user_id,))

    # Delete related feed_supplied records
    cursor.execute('DELETE FROM feed_supplied WHERE user_id = %s', (user_id,))

    # Delete related ponds
    cursor.execute('DELETE FROM ponds WHERE site_id IN (SELECT id FROM sites WHERE user_id = %s)', (user_id,))

    # Delete related sites
    cursor.execute('DELETE FROM sites WHERE user_id = %s', (user_id,))

    # Delete the user
    cursor.execute('DELETE FROM users WHERE id = %s', (user_id,))

    mysql.connection.commit()
    cursor.close()

    return redirect(url_for('admin'))

@app.route('/reset_password/<int:user_id>', methods=['POST'])
def reset_password(user_id):
    new_password = request.form.get('new_password')
    if new_password:
        hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        cursor = mysql.connection.cursor()
        cursor.execute('UPDATE users SET password = %s WHERE id = %s', (hashed_password, user_id))
        mysql.connection.commit()
        cursor.close()
        flash('Password has been reset successfully.', 'success')
    else:
        flash('Password reset was cancelled.', 'danger')
    return redirect(url_for('admin'))

@app.route('/admin/add_feed_supplied', methods=['POST'])
def add_feed_supplied():
    user_id = request.form.get('user_id')
    feed_code = request.form.get('feed_code')
    quantity_in_kg = request.form.get('quantity_in_kg')

    if not user_id or not feed_code or not quantity_in_kg:
        flash('All fields are required!', 'error')
        return redirect(url_for('admin'))

    try:
        cursor = mysql.connection.cursor()
        cursor.execute('''
            INSERT INTO feed_supplied (user_id, feed_code, quantity_in_kg)
            VALUES (%s, %s, %s)
        ''', (user_id, feed_code, quantity_in_kg))

        mysql.connection.commit()
        cursor.close()
        flash('Feed supplied successfully!', 'success')
    except Exception as e:
        flash(f'An error occurred: {str(e)}', 'error')
        mysql.connection.rollback()
    return redirect(url_for('admin'))

@app.route('/user/add_consumed_quantity', methods=['POST'])
def add_consumed_quantity():
    site_id = request.form.get('site_id')
    feed_code = request.form.get('feed_code')
    quantity = float(request.form.get('quantity_consumed'))

    cursor = mysql.connection.cursor()

    try:
        # Check if the site_id exists in the sites table
        cursor.execute('SELECT id FROM sites WHERE id = %s', (site_id,))
        site = cursor.fetchone()

        if site is None:
            return jsonify({"success": False, "message": f"The site with ID {site_id} does not exist."})

        # Check if there's an existing entry for the same site_id and feed_code
        cursor.execute('''
            SELECT quantity_in_kg FROM site_feed WHERE site_id = %s AND feed_code = %s
        ''', (site_id, feed_code))
        existing_entry = cursor.fetchone()

        if existing_entry:
            # Update the existing entry
            new_quantity = existing_entry[0] + quantity
            cursor.execute('''
                UPDATE site_feed SET quantity_in_kg = %s WHERE site_id = %s AND feed_code = %s
            ''', (new_quantity, site_id, feed_code))
        else:
            # Insert a new entry
            cursor.execute('''
                INSERT INTO site_feed (site_id, feed_code, quantity_in_kg)
                VALUES (%s, %s, %s)
            ''', (site_id, feed_code, quantity))

        mysql.connection.commit()
        return jsonify({"success": True, "reload": True})
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({"success": False, "message": str(e)})
    finally:
        cursor.close()


@app.route('/save-feed-supplied', methods=['POST'])
def save_feed_supplied():
    if not request.is_json:
        return jsonify({"success": False, "error": "Invalid data format."})

    data = request.get_json()
    user_id = data.get('user_id')
    feed_supplies = data.get('feed_supplies', [])

    if not user_id or not feed_supplies:
        return jsonify({"success": False, "error": "Invalid data."})

    try:
        with mysql.connection.cursor() as cursor:
            for item in feed_supplies:
                feed_code = item['feedCode']
                quantity = item['quantity']

                # Check if record exists
                cursor.execute('''
                    SELECT quantity_in_kg
                    FROM feed_supplied
                    WHERE user_id = %s AND feed_code = %s
                ''', (user_id, feed_code))

                existing_quantity = cursor.fetchone()

                if existing_quantity:
                    # Update existing record by adding new quantity
                    new_quantity = existing_quantity[0] + quantity
                    cursor.execute('''
                        UPDATE feed_supplied
                        SET quantity_in_kg = %s
                        WHERE user_id = %s AND feed_code = %s
                    ''', (new_quantity, user_id, feed_code))
                else:
                    # Insert new record
                    cursor.execute('''
                        INSERT INTO feed_supplied (user_id, feed_code, quantity_in_kg)
                        VALUES (%s, %s, %s)
                    ''', (user_id, feed_code, quantity))

        mysql.connection.commit()
        return jsonify({"success": True})
    except Exception as e:
        mysql.connection.rollback()
        return jsonify({"success": False, "error": str(e)})





if __name__ == '__main__':
    app.run(debug=True)
