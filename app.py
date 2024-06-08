from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_mysqldb import MySQL
from flask_bcrypt import Bcrypt
import MySQLdb.cursors
import re

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
                prawn_count VARCHAR(100) NOT NULL,
                FOREIGN KEY (site_id) REFERENCES sites(id)
            )
        ''')
        mysql.connection.commit()
        cursor.close()

# Initialize the database
init_db()

@app.route('/')
def index():
     return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        print("posttttt")
        username = request.form['username']
        password = request.form['password']
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
        user = cursor.fetchone()
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
        session['site_id_{}'.format(site_num)] = site_id
        session['num_ponds_{}'.format(site_num)] = num_ponds
        return redirect(url_for('pond_info', site_num=site_num, pond_num=1))
    return render_template('site_info.html', site_num=site_num)

@app.route('/pond_info/<int:site_num>/<int:pond_num>', methods=['GET', 'POST'])
def pond_info(site_num, pond_num):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        area = float(request.form['area'])
        prawn_count = request.form['prawn_count']
        site_id = session['site_id_{}'.format(site_num)]
        cursor = mysql.connection.cursor()
        cursor.execute('INSERT INTO ponds (site_id, area, prawn_count) VALUES (%s, %s, %s)', (site_id, area, prawn_count))
        mysql.connection.commit()
        if pond_num < session['num_ponds_{}'.format(site_num)]:
            return redirect(url_for('pond_info', site_num=site_num, pond_num=pond_num + 1))
        elif site_num < session['num_sites']:
            return redirect(url_for('site_info', site_num=site_num + 1))
        else:
            return redirect(url_for('summary'))
    return render_template('pond_info.html', site_num=site_num, pond_num=pond_num)

@app.route('/summary')
def summary():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    
    # Fetch user details
    cursor.execute('SELECT username, mobile FROM users WHERE id = %s', (session['id'],))
    user = cursor.fetchone()

    # Fetch overall summary
    cursor.execute('SELECT COUNT(*) as total_ponds, SUM(ponds.area) as total_area, SUM(ponds.prawn_count) as total_prawn_count FROM ponds JOIN sites ON ponds.site_id = sites.id WHERE sites.user_id = %s', (session['id'],))
    overall = cursor.fetchone()

    # Fetch site details
    cursor.execute('SELECT id, name, location FROM sites WHERE user_id = %s', (session['id'],))
    sites = cursor.fetchall()
    
    site_data = []
    for site in sites:
        cursor.execute('SELECT area, prawn_count FROM ponds WHERE site_id = %s', (site['id'],))
        ponds = cursor.fetchall()
        site['ponds'] = ponds
        site_data.append(site)
    
    return render_template('summary.html', 
                           username=user['username'], 
                           mobile=user['mobile'], 
                           total_ponds=overall['total_ponds'], 
                           total_area=overall['total_area'], 
                           total_prawn_count=overall['total_prawn_count'],
                           sites=site_data)



if __name__ == '__main__':
    app.run(debug=True)
