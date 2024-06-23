import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
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

# Database initialization
def init_db():
    with app.app_context():
        cursor = mysql.connection.cursor()
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
                creation_date DATE NOT NULL DEFAULT CURRENT_DATE,
                current_day INT NOT NULL,
                FOREIGN KEY (site_id) REFERENCES sites(id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS currentdate (
                id INT AUTO_INCREMENT PRIMARY KEY,
                current_day INT NOT NULL,
                currentdate DATE NOT NULL
            )
        ''')
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

    scale_factor = prawn_count / 1000.0
    scaled_feed_details = {
        "feed_per_day": feed_info["feed_per_day"] * scale_factor,
        "feed_increase_per_day": feed_info["feed_increase_per_day"],
        "accumulated_feed": feed_info["accumulated_feed"] * scale_factor,
        "feed_code": feed_info["feed_code"]
    }

    return scaled_feed_details


@app.route('/download_feed_sheet')
def download_feed_sheet():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    
    # Fetch site and pond details
    cursor.execute('''
        SELECT sites.id as site_id, sites.name, sites.location, ponds.id as pond_id, ponds.area, ponds.prawn_count, ponds.creation_date
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
            pond_feed_data.append({
                'Day': day,
                'Feed Per Day (kg)': feed_details['feed_per_day'],
                'Feed Increase Per Day (kg)': feed_details['feed_increase_per_day'],
                'Accumulated Feed (kg)': feed_details['accumulated_feed'],
                'Feed Code': feed_details['feed_code']
            })
        
        pond_df = pd.DataFrame(pond_feed_data)
        feed_sheets.append({
            'pond_id': pond['pond_id'],
            'pond_data': pond_df
        })
    
    # Create a Pandas Excel writer using an in-memory buffer
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        for sheet in feed_sheets:
            sheet['pond_data'].to_excel(writer, sheet_name=f'Pond {sheet["pond_id"]}', index=False)
    
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
            flash('Invalid username or password!', 'danger')
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
        session['num_sites'] = num_sites
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
        cursor = mysql.connection.cursor()
        cursor.execute('INSERT INTO sites (user_id, name, location) VALUES (%s, %s, %s)', (session['id'], site_name, location))
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



@app.route('/summary')
def summary():
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    
    # Fetch user details
    cursor.execute('SELECT username, mobile FROM users WHERE id = %s', (session['id'],))
    user = cursor.fetchone()

    # Fetch overall summary
    cursor.execute('''
        SELECT COUNT(*) as total_ponds, SUM(ponds.area) as total_area, SUM(ponds.prawn_count) as total_prawn_count 
        FROM ponds 
        JOIN sites ON ponds.site_id = sites.id 
        WHERE sites.user_id = %s
    ''', (session['id'],))
    overall = cursor.fetchone()

    # Fetch site and pond details
    cursor.execute('''
        SELECT sites.id as site_id, sites.name, sites.location, ponds.id as pond_id, ponds.area, ponds.prawn_count, ponds.creation_date
        FROM sites
        JOIN ponds ON sites.id = ponds.site_id
        WHERE sites.user_id = %s
    ''', (session['id'],))
    ponds = cursor.fetchall()
    
    cursor.close()
    
    site_data = {}
    today_date = datetime.datetime.now().date()
    for pond in ponds:
        site_id = pond['site_id']
        if site_id not in site_data:
            site_data[site_id] = {
                'id': site_id,
                'name': pond['name'],
                'location': pond['location'],
                'total_area': 0,
                'total_prawn_count': 0,
                'ponds': []
            }

        site_data[site_id]['total_area'] += pond['area']
        site_data[site_id]['total_prawn_count'] += pond['prawn_count']

        # Calculate current_day for each pond based on its creation_date
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
        else:
            pond.update({
                'feed_per_day': 'N/A',
                'feed_increase_per_day': 'N/A',
                'accumulated_feed': 'N/A',
                'feed_code': 'N/A',
                'current_day': 'N/A'
            })
        
        pond['creation_date'] = pond['creation_date'].strftime('%Y-%m-%d') if pond['creation_date'] else 'N/A'
        site_data[site_id]['ponds'].append(pond)
    
    return render_template('summary.html', 
                           username=user['username'], 
                           mobile=user['mobile'], 
                           current_day='Varies',  # Each pond has its own current day
                           total_ponds=overall['total_ponds'], 
                           total_area=overall['total_area'], 
                           total_prawn_count=overall['total_prawn_count'],
                           sites=list(site_data.values()))





if __name__ == '__main__':
    app.run(debug=True)
