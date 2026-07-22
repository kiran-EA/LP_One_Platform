"""
LampsPlus File Validation Application
Flask-based SFTP file validation system
"""

import os
import io
import zipfile
import csv
import uuid
import threading
import smtplib
import warnings
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
warnings.filterwarnings('ignore', message='.*TripleDES.*')
warnings.filterwarnings('ignore', message='.*cryptography.*')
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, stream_with_context
import json
import paramiko
import psycopg2

IST = ZoneInfo('Asia/Kolkata')

# ==================== REDSHIFT CONNECTION ====================

REDSHIFT_CONFIG = {
    'host':     'ea-non-prod.cxw4zfxatj9b.us-west-1.redshift.amazonaws.com',
    'port':     5439,
    'database': 'express',
    'user':     'easuper',
    'password': 'LAMRedPWD@2024',
}

CAMPAIGN_DATE_COLS = [
    'campaign_start_date',
    'campaign_end_date',
    'campaign_drop_date',
    'cdi_full_refresh_send',
    'cdi_full_refresh_receive',
    'modeling_kick_off',
    'final_model_due',
    'lp_score_approval',
    'final_scoring',
    'lp_count_approval',
    'ea_merge_purge_delivery',
    'cumm_cell',
    'circ_plan',
    'mail_file',
    'hygine_files',
    'ntf_estimate_rank_score',
    'ntf_actual_rank_score',
    'ea_ntf_merge_purge_delvr',
    'ntf_mail_file',
    'ntf_hygine_files',
    'ntf_cumm_cell',
    'gross_in',
    'campaign_ntf_start_date',
]


def _redshift_connect():
    return psycopg2.connect(
        host=REDSHIFT_CONFIG['host'],
        port=REDSHIFT_CONFIG['port'],
        database=REDSHIFT_CONFIG['database'],
        user=REDSHIFT_CONFIG['user'],
        password=REDSHIFT_CONFIG['password'],
        connect_timeout=20,
        sslmode='require',
    )

# In-memory job store: {job_id: {'status': 'running'|'done'|'error', 'results': [...], 'progress': 'msg'}}
_jobs = {}
_jobs_lock = threading.Lock()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'lampsplus-local-dev-key-2026')
app.config.update(
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_NAME='lp_session',
)

# Configuration
SFTP_CONFIG = {
    'hostname': 'ftp1.lampsplus.com',
    'port': 9822,
    'username': 'ExpressAnalytic',
    'key_path': os.path.join(os.path.dirname(__file__), 'keys', 'lp_key')
}

# Authentication credentials
VALID_CREDENTIALS = {
    'username': 'directmarketing',
    'password': 'Lampsplus!1901'
}

TEMP_DIR = os.environ.get('TEMP_DIR', '/tmp/lp_monitor')
os.makedirs(TEMP_DIR, exist_ok=True)


def get_sftp_connection():
    """Establish SFTP connection using SSH key"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Load key from env variable (Vercel) or fall back to local file
        key_content = os.environ.get('SFTP_PRIVATE_KEY')
        if key_content:
            # Vercel may store newlines as literal \n — normalize them
            key_content = key_content.replace('\\n', '\n')
            private_key = paramiko.RSAKey.from_private_key(io.StringIO(key_content))
        else:
            private_key = paramiko.RSAKey.from_private_key_file(SFTP_CONFIG['key_path'])

        ssh.connect(
            hostname=SFTP_CONFIG['hostname'],
            port=SFTP_CONFIG['port'],
            username=SFTP_CONFIG['username'],
            pkey=private_key,
            look_for_keys=False,
            allow_agent=False,
            banner_timeout=60,
            auth_timeout=60,
            timeout=60
        )

        # Send keepalive every 30s to prevent connection drops on large files
        transport = ssh.get_transport()
        transport.set_keepalive(30)
        transport.window_size = 4 * 1024 * 1024       # 4 MB window
        transport.packetizer.REKEY_BYTES = pow(2, 40)  # disable rekey during transfer

        sftp = ssh.open_sftp()
        sftp.get_channel().settimeout(None)  # no timeout — keepalive handles drops
        return ssh, sftp
    except Exception as e:
        raise Exception(f"SFTP Connection Error: {str(e)}")


def list_sftp_files(remote_path):
    """List all files from SFTP directory"""
    try:
        ssh, sftp = get_sftp_connection()
        
        # List directory
        files = []
        try:
            file_list = sftp.listdir(remote_path)
            for filename in file_list:
                try:
                    file_stat = sftp.stat(f"{remote_path}/{filename}")
                    files.append({
                        'name': filename,
                        'size': file_stat.st_size,
                        'modified': datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    })
                except:
                    # If stat fails, just add the filename
                    files.append({
                        'name': filename,
                        'size': 0,
                        'modified': 'N/A'
                    })
        except FileNotFoundError:
            return {'error': f'Directory not found: {remote_path}'}
        finally:
            sftp.close()
            ssh.close()
        
        return {'files': sorted(files, key=lambda x: x['name'])}
    except Exception as e:
        return {'error': str(e)}


def download_and_process_file(remote_path, filename, retries=3):
    """Download file from SFTP and process it, with retry on connection drop"""
    last_error = None
    for attempt in range(1, retries + 1):
        ssh, sftp = None, None
        try:
            ssh, sftp = get_sftp_connection()
            remote_file_path = f"{remote_path}/{filename}"
            file_data = io.BytesIO()
            sftp.getfo(remote_file_path, file_data)
            file_data.seek(0)

            if filename.lower().endswith('.zip'):
                return process_zip_file(file_data, filename)
            else:
                return {'error': 'Only ZIP files are supported'}

        except Exception as e:
            import traceback
            last_error = str(e)
            log_path = os.path.join(os.path.dirname(__file__), 'sftp_error.log')
            with open(log_path, 'a') as lf:
                lf.write(f"[Attempt {attempt}/{retries}] {filename}: {last_error}\n")
                traceback.print_exc(file=lf)
        finally:
            try:
                if sftp: sftp.close()
                if ssh: ssh.close()
            except Exception:
                pass

    return {'error': f'Download Error (after {retries} attempts): {last_error}'}


EXPECTED_COLUMNS = [
    'CustNo', 'Keycode', 'Name', 'Company', 'Address3', 'Address2',
    'Address1', 'City', 'State', 'ZIP', 'Message1', 'Message2',
    'Message3', 'Message4', 'Message5', 'Message6', 'Message7', 'Message8',
    'Message9', 'Message10', 'ORGRecNo', 'RecNo', 'File'
]


def detect_delimiter(line):
    """Detect delimiter from a header line"""
    candidates = [('|', line.count('|')), (',', line.count(',')),
                  ('\t', line.count('\t')), (';', line.count(';'))]
    best = max(candidates, key=lambda x: x[1])
    return best[0] if best[1] > 0 else ','


def process_zip_file(file_data, zip_filename):
    """Extract and read headers from ZIP file"""
    try:
        results = []

        with zipfile.ZipFile(file_data, 'r') as zip_ref:
            file_list = zip_ref.namelist()

            for file_name in file_list:
                if file_name.endswith('/') or file_name.startswith('.'):
                    continue

                with zip_ref.open(file_name) as f:
                    content = f.read()

                    try:
                        text_content = content.decode('utf-8')
                        lines = text_content.strip().split('\n')

                        if not lines:
                            results.append({'filename': file_name, 'header': [], 'row_count': 0, 'status': 'error: empty file'})
                            continue

                        header_line = lines[0]
                        delimiter = detect_delimiter(header_line)

                        reader = csv.reader([header_line], delimiter=delimiter)
                        header = [col.strip() for col in next(reader)]

                        # Column name validation
                        columns_valid = header == EXPECTED_COLUMNS

                        # Null % for CustNo and Keycode
                        custno_null_pct = None
                        keycode_null_pct = None
                        data_rows = len(lines) - 1  # exclude header

                        if data_rows > 0 and header:
                            custno_idx = header.index('CustNo') if 'CustNo' in header else None
                            keycode_idx = header.index('Keycode') if 'Keycode' in header else None

                            custno_null = 0
                            keycode_null = 0

                            for line in lines[1:]:
                                row_reader = csv.reader([line], delimiter=delimiter)
                                try:
                                    row = next(row_reader)
                                except StopIteration:
                                    continue
                                if custno_idx is not None:
                                    val = row[custno_idx].strip() if custno_idx < len(row) else ''
                                    if val == '':
                                        custno_null += 1
                                if keycode_idx is not None:
                                    val = row[keycode_idx].strip() if keycode_idx < len(row) else ''
                                    if val == '':
                                        keycode_null += 1

                            custno_null_pct = round(custno_null / data_rows * 100, 2) if custno_idx is not None else None
                            keycode_null_pct = round(keycode_null / data_rows * 100, 2) if keycode_idx is not None else None

                        delimiter_display = {
                            '|': 'Pipe (|)', ',': 'Comma (,)',
                            '\t': 'Tab', ';': 'Semicolon (;)'
                        }.get(delimiter, delimiter)

                        results.append({
                            'filename': file_name,
                            'header': header,
                            'row_count': len(lines),
                            'delimiter': delimiter_display,
                            'columns_valid': columns_valid,
                            'custno_null_pct': custno_null_pct,
                            'keycode_null_pct': keycode_null_pct,
                            'status': 'success'
                        })
                    except Exception as e:
                        results.append({
                            'filename': file_name,
                            'header': [],
                            'row_count': 0,
                            'status': f'error: {str(e)}'
                        })

        return {
            'zip_file': zip_filename,
            'files': results
        }
    except Exception as e:
        return {'error': f'ZIP Processing Error: {str(e)}'}


# ==================== ROUTES ====================

@app.route('/')
def index():
    """Redirect to login page"""
    if 'logged_in' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page and authentication"""
    if request.method == 'POST':
        data = request.json
        username = data.get('username', '')
        password = data.get('password', '')
        
        if (username == VALID_CREDENTIALS['username'] and 
            password == VALID_CREDENTIALS['password']):
            session['logged_in'] = True
            session['username'] = username
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Invalid credentials'})
    
    return render_template('login.html')


@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard')
def dashboard():
    """Main dashboard page"""
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html', username=session.get('username'))


@app.route('/api/list-files')
def api_list_files():
    """API endpoint to list SFTP files"""
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    remote_path = request.args.get('path', '/FromLP/Catalog Mail Files')
    result = list_sftp_files(remote_path)
    return jsonify(result)


@app.route('/api/process-files', methods=['POST'])
def api_process_files():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.json
    files = data.get('files', [])
    remote_path = data.get('path', '/FromLP/Catalog Mail Files')
    job_id = str(uuid.uuid4())

    with _jobs_lock:
        _jobs[job_id] = {'status': 'running', 'results': [], 'progress': f'Starting ({len(files)} file(s))...'}

    def run():
        results = []
        for i, filename in enumerate(files):
            with _jobs_lock:
                _jobs[job_id]['progress'] = f'Downloading {filename} ({i+1}/{len(files)})...'
            result = download_and_process_file(remote_path, filename)
            results.append(result)
        with _jobs_lock:
            _jobs[job_id] = {'status': 'done', 'results': results, 'progress': 'Complete'}

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/api/job-status/<job_id>')
def api_job_status(job_id):
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(job)


# ==================== CIRCPLAN ROUTES ====================

CIRCPLAN_EXPECTED_COLUMNS = [
    'Key Code', 'SegKey', 'Customer Type', 'List Name', 'Rec', '$',
    'FREQ', 'Version Type', 'Broker', 'OPT 1', 'OPT 2', 'OPT3',
    'Gross Qty', 'Quantity Mailed', 'LIST COST', 'Sub-Category',
    'Campaign_Name', 'Start_Date', 'End_Date'
]

CIRCPLAN_SERVER = {
    'hostname': '54.176.67.86',
    'port': 22,
    'username': 'eapcprod',
    'password': '6trKdbLw',
    'script_dir': '/app/share/Informatica/scripts/bin/CircPlan',
    'script': 'circ_plan_load_new_v2.sh'
}


def _parse_circplan_content(content_bytes, filename):
    """Parse a CircPlan CSV/TXT content and run QC checks"""
    try:
        try:
            text = content_bytes.decode('utf-8-sig')  # strips BOM if present
        except UnicodeDecodeError:
            text = content_bytes.decode('latin-1')

        lines = [l.rstrip('\r') for l in text.strip().split('\n')]
        if not lines:
            return {'filename': filename, 'header': [], 'row_count': 0, 'status': 'error: empty file'}

        delimiter = detect_delimiter(lines[0])
        reader = csv.reader([lines[0]], delimiter=delimiter)

        def _norm(c):
            # collapse all whitespace variants (including non-breaking space) to single space
            return ' '.join(c.replace('\xa0', ' ').split())

        raw_header = [col.strip() for col in next(reader)]
        header_norm = [_norm(col) for col in raw_header]
        expected_norm = [_norm(col) for col in CIRCPLAN_EXPECTED_COLUMNS]
        columns_valid = header_norm == expected_norm
        header = raw_header  # keep original for display

        # Key Code null %
        keycode_null_pct = None
        data_rows = len(lines) - 1
        kc_key = next((c for c in header if _norm(c) == _norm('Key Code')), None)
        if data_rows > 0 and kc_key:
            kc_idx = header.index(kc_key)
            kc_null = 0
            for line in lines[1:]:
                rdr = csv.reader([line], delimiter=delimiter)
                try:
                    row = next(rdr)
                    if kc_idx >= len(row) or row[kc_idx].strip() == '':
                        kc_null += 1
                except StopIteration:
                    continue
            keycode_null_pct = round(kc_null / data_rows * 100, 2)

        delimiter_display = {
            '|': 'Pipe (|)', ',': 'Comma (,)',
            '\t': 'Tab', ';': 'Semicolon (;)'
        }.get(delimiter, delimiter)

        return {
            'filename': filename,
            'header': header,
            'row_count': len(lines),
            'delimiter': delimiter_display,
            'columns_valid': columns_valid,
            'keycode_null_pct': keycode_null_pct,
            'status': 'success'
        }
    except Exception as e:
        return {'filename': filename, 'header': [], 'row_count': 0, 'status': f'error: {str(e)}'}


def process_circplan_file(file_data, filename):
    """Extract and QC a CircPlan file (ZIP or direct CSV/TXT)"""
    try:
        content_bytes = file_data.read()

        if zipfile.is_zipfile(io.BytesIO(content_bytes)):
            results = []
            with zipfile.ZipFile(io.BytesIO(content_bytes), 'r') as zf:
                for inner in zf.namelist():
                    if inner.endswith('/') or inner.startswith('.'):
                        continue
                    with zf.open(inner) as f:
                        results.append(_parse_circplan_content(f.read(), inner))
            return {'zip_file': filename, 'files': results}

        return {'zip_file': filename, 'files': [_parse_circplan_content(content_bytes, filename)]}
    except Exception as e:
        return {'error': f'Processing Error: {str(e)}'}


def circplan_download_and_process(filename, retries=3):
    """Download and process a CircPlan file from SFTP"""
    remote_path = '/FromLP/Circ Plans'
    last_error = None
    for attempt in range(1, retries + 1):
        ssh, sftp = None, None
        try:
            ssh, sftp = get_sftp_connection()
            file_data = io.BytesIO()
            sftp.getfo(f"{remote_path}/{filename}", file_data)
            file_data.seek(0)
            return process_circplan_file(file_data, filename)
        except Exception as e:
            last_error = str(e)
        finally:
            try:
                if sftp: sftp.close()
                if ssh: ssh.close()
            except Exception:
                pass
    return {'error': f'Download Error (after {retries} attempts): {last_error}'}


@app.route('/api/circplan/list-files')
def api_circplan_list_files():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    result = list_sftp_files('/FromLP/Circ Plans')
    return jsonify(result)


@app.route('/api/circplan/process-files', methods=['POST'])
def api_circplan_process_files():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    data = request.json
    files = data.get('files', [])
    job_id = str(uuid.uuid4())

    with _jobs_lock:
        _jobs[job_id] = {'status': 'running', 'results': [], 'progress': f'Starting ({len(files)} file(s))...'}

    def run():
        results = []
        for i, f in enumerate(files):
            with _jobs_lock:
                _jobs[job_id]['progress'] = f'Downloading {f} ({i+1}/{len(files)})...'
            results.append(circplan_download_and_process(f))
        with _jobs_lock:
            _jobs[job_id] = {'status': 'done', 'results': results, 'progress': 'Complete'}

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/api/circplan/start-script', methods=['POST'])
def api_circplan_start_script():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    session['cp_script_params'] = request.json
    return jsonify({'ok': True})


@app.route('/api/circplan/stream')
def api_circplan_stream():
    if 'logged_in' not in session:
        return Response('data: {"line":"Not authenticated","done":true}\n\n',
                        content_type='text/event-stream')

    params = session.get('cp_script_params', {})
    camp_name    = params.get('camp_name', '').strip()
    is_ntf       = params.get('is_ntf', 'n').strip()
    keycode_file = params.get('keycode_file', '').strip()
    zip_type     = params.get('zip_type', 'combined').strip()
    mail_file    = params.get('mail_file', '').strip()
    mail_files   = params.get('mail_files', '').strip()

    # Build all stdin inputs (initial prompts + 'y' for any mid-script confirmations)
    mail_input = mail_file if zip_type == 'combined' else mail_files
    stdin_inputs = '\n'.join([camp_name, is_ntf, keycode_file, zip_type,
                               mail_input, 'y', 'y', 'y', 'y']) + '\n'

    def generate():
        ssh = None
        try:
            yield f"data: {json.dumps({'line': '--- Connecting to server 54.176.67.86 ...'})}\n\n"
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(CIRCPLAN_SERVER['hostname'], port=CIRCPLAN_SERVER['port'],
                        username=CIRCPLAN_SERVER['username'],
                        password=CIRCPLAN_SERVER['password'], timeout=30)

            yield f"data: {json.dumps({'line': '--- Connected. Launching script...'})}\n\n"

            cmd = (f"cd {CIRCPLAN_SERVER['script_dir']} && "
                   f"sh {CIRCPLAN_SERVER['script']}")
            stdin, stdout, stderr = ssh.exec_command(cmd, timeout=3600)
            stdin.write(stdin_inputs)
            stdin.channel.shutdown_write()

            for line in iter(stdout.readline, ''):
                if line:
                    yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
            for line in iter(stderr.readline, ''):
                if line:
                    yield f"data: {json.dumps({'line': '[ERR] ' + line.rstrip()})}\n\n"

            exit_code = stdout.channel.recv_exit_status()
            status = 'completed successfully' if exit_code == 0 else f'exited with code {exit_code}'
            yield f"data: {json.dumps({'line': f'--- Script {status}', 'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'line': f'ERROR: {str(e)}', 'done': True})}\n\n"
        finally:
            if ssh:
                try: ssh.close()
                except: pass

    return Response(stream_with_context(generate()),
                    content_type='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/mailfile/start-script', methods=['POST'])
def api_mailfile_start_script():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    session['mf_script_params'] = request.json
    return jsonify({'ok': True})


@app.route('/api/mailfile/stream')
def api_mailfile_stream():
    if 'logged_in' not in session:
        return Response('data: {"line":"Not authenticated","done":true}\n\n',
                        content_type='text/event-stream')

    params    = session.get('mf_script_params', {})
    camp_name = params.get('camp_name', '').strip()

    def generate():
        ssh = None
        try:
            yield f"data: {json.dumps({'line': '--- Connecting to server 54.176.67.86 ...'})}\n\n"
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(CIRCPLAN_SERVER['hostname'], port=CIRCPLAN_SERVER['port'],
                        username=CIRCPLAN_SERVER['username'],
                        password=CIRCPLAN_SERVER['password'], timeout=30)

            yield f"data: {json.dumps({'line': '--- Connected. Launching mail_file_load.sh ...'})}\n\n"

            cmd = (f"cd {CIRCPLAN_SERVER['script_dir']} && "
                   f"export camp_name={camp_name!r} && sh mail_file_load.sh")
            stdin, stdout, stderr = ssh.exec_command(cmd, timeout=3600)
            stdin.channel.shutdown_write()

            for line in iter(stdout.readline, ''):
                if line:
                    yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
            for line in iter(stderr.readline, ''):
                if line:
                    yield f"data: {json.dumps({'line': '[ERR] ' + line.rstrip()})}\n\n"

            exit_code = stdout.channel.recv_exit_status()
            status = 'completed successfully' if exit_code == 0 else f'exited with code {exit_code}'
            yield f"data: {json.dumps({'line': f'--- Script {status}', 'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'line': f'ERROR: {str(e)}', 'done': True})}\n\n"
        finally:
            if ssh:
                try: ssh.close()
                except: pass

    return Response(stream_with_context(generate()),
                    content_type='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ==================== SERVER TERMINAL ====================

def _ssh_connect():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(CIRCPLAN_SERVER['hostname'], port=CIRCPLAN_SERVER['port'],
                username=CIRCPLAN_SERVER['username'],
                password=CIRCPLAN_SERVER['password'], timeout=15)
    return ssh


@app.route('/server-terminal')
def server_terminal():
    if 'logged_in' not in session:
        return redirect(url_for('login'))
    return render_template('server_terminal.html', username=session.get('username', ''))


@app.route('/api/server/ls')
def api_server_ls():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    path = request.args.get('path', '/app/share/Informatica/scripts/bin').strip()
    try:
        ssh = _ssh_connect()
        cmd = f"ls -la {path!r} 2>&1"
        _, stdout, _ = ssh.exec_command(cmd)
        raw = stdout.read().decode(errors='replace')
        ssh.close()

        entries = []
        for line in raw.splitlines():
            if line.startswith('total') or not line.strip():
                continue
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            perms, _, _, _, size, month, day, time_or_year, name = parts
            is_dir = perms.startswith('d')
            is_hidden = name.startswith('.')
            entries.append({
                'name': name,
                'is_dir': is_dir,
                'is_hidden': is_hidden,
                'size': size,
                'modified': f"{month} {day} {time_or_year}",
                'perms': perms,
            })
        return jsonify({'path': path, 'entries': entries})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/server/cat')
def api_server_cat():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    path = request.args.get('path', '').strip()
    if not path:
        return jsonify({'error': 'No path provided'}), 400
    try:
        ssh = _ssh_connect()
        _, stdout, stderr = ssh.exec_command(f"cat {path!r} 2>&1 | head -500")
        content = stdout.read().decode(errors='replace')
        ssh.close()
        return jsonify({'path': path, 'content': content})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/server/search')
def api_server_search():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    base = request.args.get('path', '/app/share/Informatica/scripts/bin').strip()
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': 'No search query'}), 400
    try:
        ssh = _ssh_connect()
        cmd = f"find {base!r} -maxdepth 3 -name {('*'+query+'*')!r} 2>/dev/null | head -100"
        _, stdout, _ = ssh.exec_command(cmd)
        results = [l.strip() for l in stdout.read().decode(errors='replace').splitlines() if l.strip()]
        ssh.close()
        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/server/exec', methods=['POST'])
def api_server_exec():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    data = request.json or {}
    cmd = data.get('cmd', '').strip()
    if not cmd:
        return jsonify({'error': 'No command'}), 400
    # block destructive commands
    blocked = ['rm ', 'rmdir', 'mkfs', '> /', 'dd if', 'chmod 777 /', 'chown']
    if any(b in cmd for b in blocked):
        return jsonify({'error': 'Command blocked for safety'}), 403
    try:
        ssh = _ssh_connect()
        _, stdout, stderr = ssh.exec_command(cmd, timeout=30)
        out = stdout.read().decode(errors='replace')
        err = stderr.read().decode(errors='replace')
        ssh.close()
        return jsonify({'output': out + (('\n[stderr]: ' + err) if err.strip() else '')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== DAILY QC MONITOR: EA INCOMING FILES - WEB DATA ====================

WEBORDERS_PATH = '/app/share/data/staging/weborders'
QC_LOW_COUNT_THRESHOLD = 10
_COMPACT_TIME_SLOTS = ['0230', '0830', '1430', '2030']
_UNDERSCORED_TIME_SLOTS = ['02_30', '08_30', '14_30', '20_30']

# (base filename, pattern type, ignore-from-QC flag)
# pattern types:
#   daily             -> {base}_{YYYY_MM_DD}.txt
#   compact_time      -> {base}_{YYYYMMDD}{HHMM}.txt    (4 runs/day)
#   underscored_time  -> {base}_{YYYY_MM_DD}_{HH_MM}.txt (4 runs/day)
WEB_DATA_FILES = [
    ('AvailableOptOut_Data', 'daily', False),
    ('CompanyAccess_Data', 'daily', False),
    ('Company_Data', 'daily', False),
    ('CompanyHistory_Data', 'daily', False),
    ('CompanyResaleLicense', 'daily', False),
    ('DeleteMyPersonalInfo', 'daily', False),
    ('DomExportInvoice', 'daily', False),
    ('DomExportInvoiceItemHistory', 'daily', False),
    ('DomExportInvoiceItemsr', 'daily', False),
    ('DomExportOrderPaymentHistory', 'daily', False),
    ('DomOrderHeader', 'daily', False),
    ('DomOrderHeaderHistory', 'daily', False),
    ('DomOrderPayment', 'compact_time', False),
    ('EmailRecepients', 'daily', False),
    ('EmployeeInfo_Data', 'daily', False),
    ('EmployeeShortName_Data', 'daily', False),
    ('GlobalCart', 'underscored_time', False),
    ('Lamps_COA', 'daily', False),
    ('Locations_Data', 'daily', False),
    ('MS_MC_WarrantyMessages', 'daily', True),
    ('MS_PastWarrantyMessages', 'daily', True),
    ('MS_RT_LPEReceipt', 'daily', True),
    ('MS_RT_LPOBOrderConfirm', 'daily', True),
    ('MS_RT_LPOBShipConfirm', 'daily', True),
    ('MS_RT_LPOrderConfirm', 'daily', True),
    ('MS_RT_LPPreWarranty', 'daily', True),
    ('MS_RT_LPShipConfirm', 'daily', True),
    ('MS_RT_LPWarranty', 'daily', True),
    ('MS_TrackingContexts', 'daily', False),
    ('Portfolio_Data', 'daily', False),
    ('PortfolioItems_Data', 'daily', False),
    ('ProductReviews', 'daily', False),
    ('Products_Data', 'daily', False),
    ('ProductsExtra_Data', 'daily', False),
    ('PromoCodes_Data', 'daily', False),
    ('PromoCodesHistory_Data', 'daily', False),
    ('PromoTermsCondition_Data', 'daily', True),
    ('PromoTermsConditionHistory_Data', 'daily', True),
    ('Promotions_Data', 'daily', False),
    ('PromotionsHistory_Data', 'daily', False),
    ('Request_Data', 'underscored_time', False),
    ('RewardNumber_CustomerId_Mapping', 'daily', False),
    ('SavedCarts_Data', 'daily', False),
    ('SendTopStyle', 'daily', True),
    ('SharedItems', 'daily', False),
    ('SharedItemsHistory', 'daily', True),
    ('ShippingAddress_Data', 'compact_time', False),
    ('ShippingAddressHistory_Data', 'compact_time', False),
    ('Source_Data', 'daily', False),
    ('SubLocationCode_Data', 'daily', False),
    ('UserHistory_Data', 'daily', False),
    ('UserOptOut_Data', 'daily', False),
    ('UserOptOutHistory_Data', 'daily', False),
    ('UserProfile_Data', 'compact_time', False),
    ('UserProfileHistory_Data', 'compact_time', False),
    ('Warranty_Data', 'daily', False),
]


POS_DATA_PATH = '/app/share/sourcefiles_new/Lampsplus/AS400_Files/staging'

# (base filename, pattern type, ignore-from-QC flag)
# pattern types:
#   seq1 -> {base}_{YYYY_MM_DD}_01.txt          (single run/day)
#   seq4 -> {base}_{YYYY_MM_DD}_{01..04}.txt     (4 runs/day)
POS_DATA_FILES = [
    ('CIMCATL', 'seq1', False),
    ('CIMCOUP', 'seq4', False),
    ('CIMCUST', 'seq4', False),
    ('CIMDCSC', 'seq1', False),
    ('CIMPOSH', 'seq4', False),
    ('CIMPOSI', 'seq4', False),
    ('CIMPOST', 'seq4', False),
    ('CIMPOSW', 'seq4', False),
    ('CIMPROD', 'seq1', False),
    ('CIMPROM', 'seq1', False),
    ('CIMREWXREF', 'seq1', True),
    ('CIMSLSM', 'seq1', False),
    ('CIMSTORE', 'seq1', False),
    ('CIMVEND', 'seq1', True),
]


def _ist_yesterday():
    """Return yesterday's date in IST (the date whose feed files we expect today)."""
    return (datetime.now(IST) - timedelta(days=1)).date()


def _web_data_expected_files(check_date):
    """Build the expected filename list for check_date. Returns [(filename, ignore), ...]."""
    ymd_ = check_date.strftime('%Y_%m_%d')
    ymd = check_date.strftime('%Y%m%d')
    expected = []
    for base, ptype, ignore in WEB_DATA_FILES:
        if ptype == 'daily':
            expected.append((f"{base}_{ymd_}.txt", ignore))
        elif ptype == 'compact_time':
            for slot in _COMPACT_TIME_SLOTS:
                expected.append((f"{base}_{ymd}{slot}.txt", ignore))
        elif ptype == 'underscored_time':
            for slot in _UNDERSCORED_TIME_SLOTS:
                expected.append((f"{base}_{ymd_}_{slot}.txt", ignore))
    return expected


def _pos_data_expected_files(check_date):
    """Build the expected POS filename list for check_date. Returns [(filename, ignore), ...]."""
    ymd_ = check_date.strftime('%Y_%m_%d')
    expected = []
    for base, ptype, ignore in POS_DATA_FILES:
        if ptype == 'seq1':
            expected.append((f"{base}_{ymd_}_01.txt", ignore))
        elif ptype == 'seq4':
            for n in range(1, 5):
                expected.append((f"{base}_{ymd_}_{n:02d}.txt", ignore))
    return expected


def _list_remote_files_with_counts(remote_path, ymd_, ymd):
    """SSH into the eapcprod server and get size + line count for files matching the date."""
    ssh = _ssh_connect()
    try:
        check_cmd = f"test -d '{remote_path}' && echo DIR_OK || echo DIR_MISSING"
        _, stdout, _ = ssh.exec_command(check_cmd, timeout=15)
        if stdout.read().decode(errors='replace').strip() != 'DIR_OK':
            raise Exception(f"Directory not found on server: {remote_path}")

        cmd = (
            "cd '" + remote_path + "' && "
            "ls -1 *" + ymd_ + "* *" + ymd + "* 2>/dev/null | sort -u | "
            "while IFS= read -r f; do "
            "printf '%s\\t%s\\t%s\\n' \"$f\" \"$(stat -c%s -- \"$f\" 2>/dev/null)\" \"$(wc -l < \"$f\" 2>/dev/null)\"; "
            "done"
        )
        _, stdout, _ = ssh.exec_command(cmd, timeout=45)
        raw = stdout.read().decode(errors='replace')

        files = {}
        for line in raw.splitlines():
            parts = line.split('\t')
            if len(parts) == 3 and parts[1].strip().isdigit() and parts[2].strip().isdigit():
                files[parts[0]] = {'size': int(parts[1]), 'count': int(parts[2])}
        return files
    finally:
        ssh.close()


def _run_file_qc_check(remote_path, expected):
    """Compare the expected filename list against what's actually on the server."""
    check_date = _ist_yesterday()
    ymd_ = check_date.strftime('%Y_%m_%d')
    ymd = check_date.strftime('%Y%m%d')
    expected_names = {name for name, _ in expected}

    actual_files = _list_remote_files_with_counts(remote_path, ymd_, ymd)

    rows = []
    issue_count = 0

    for name, ignore in expected:
        if ignore:
            continue  # hidden entirely from display and from missing/low-count alerts
        if name in actual_files:
            info = actual_files[name]
            flagged = info['count'] < QC_LOW_COUNT_THRESHOLD
            rows.append({'name': name, 'size': info['size'], 'count': info['count'],
                         'status': 'flagged' if flagged else 'ok'})
            if flagged:
                issue_count += 1
        else:
            rows.append({'name': name, 'size': None, 'count': None, 'status': 'missing'})
            issue_count += 1

    for name, info in actual_files.items():
        if name not in expected_names:
            rows.append({'name': name, 'size': info['size'], 'count': info['count'], 'status': 'unexpected'})
            issue_count += 1

    rows.sort(key=lambda r: r['name'])

    return {
        'check_date': ymd_,
        'path': remote_path,
        'rows': rows,
        'issue_count': issue_count
    }


@app.route('/api/qc/web-data-files')
def api_qc_web_data_files():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    expected = _web_data_expected_files(_ist_yesterday())
    try:
        result = _run_file_qc_check(WEBORDERS_PATH, expected)
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500
    return jsonify(result)


@app.route('/api/qc/pos-data-files')
def api_qc_pos_data_files():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    expected = _pos_data_expected_files(_ist_yesterday())
    try:
        result = _run_file_qc_check(POS_DATA_PATH, expected)
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500
    return jsonify(result)


# ==================== DAILY QC MONITOR: EA INCOMING FILES - EMAIL DATA ====================

BLUECORE_EMAIL_PATH = '/app/share/data/staging/emaildata'
WUNDERKIND_PATH = '/app/share/sourcefiles_new/Lampsplus/Wunderkind'


def _bluecore_expected_files(check_date):
    ymd = check_date.strftime('%Y%m%d')
    return [(f'bluecore_esp_event_data_{ymd}.csv', False)]


def _wunderkind_expected_files(check_date):
    ymd_ = check_date.strftime('%Y_%m_%d')
    return [
        (f'Email Events for EA_{ymd_}.csv', False),
        (f'Text Events for EA_{ymd_}.csv', False),
    ]


@app.route('/api/qc/email-bluecore')
def api_qc_email_bluecore():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    expected = _bluecore_expected_files(_ist_yesterday())
    try:
        result = _run_file_qc_check(BLUECORE_EMAIL_PATH, expected)
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500
    return jsonify(result)


@app.route('/api/qc/email-wunderkind')
def api_qc_email_wunderkind():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    expected = _wunderkind_expected_files(_ist_yesterday())
    try:
        result = _run_file_qc_check(WUNDERKIND_PATH, expected)
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500
    return jsonify(result)


# ==================== DAILY QC MONITOR: EA OUTGOING FILES ====================

LIVERAMP_PATH   = '/app/share/Target_Files/External/Lampsplus/LIVERAMP_CRM'
CDI_ARCHIVE_PATH = '/app/share/Target_Files/External/Lampsplus/CDI/archive'
CDI_RETURN_PATH  = '/app/share/sourcefiles_new/Lampsplus/CDI'
BRITEVERIFY_PATH = '/app/share/sourcefiles_new/Lampsplus/250OK'
ORACLE_FILEWATCH_PATH = '/app/share/sourcefiles_new/FileWatch'
REWARDS_PATH    = '/app/share/Target_Files/External/Lampsplus/ToLP'
PEBBLEPOST_PATH = '/app/share/Target_Files/External/Lampsplus/PebblePost'
GA_HOURLY_PATH  = '/app/share/Target_Files/External/Lampsplus/ToBluecore_Hourly'
CRITEO_PATH     = '/app/share/Target_Files/External/Lampsplus/Criteo'


def _ist_today():
    return datetime.now(IST).date()


def _list_remote_files_with_patterns(remote_path, date_patterns):
    """List files matching any of the given date string patterns, with size, line count, and mtime."""
    ssh = _ssh_connect()
    try:
        check_cmd = f"test -d '{remote_path}' && echo DIR_OK || echo DIR_MISSING"
        _, stdout, _ = ssh.exec_command(check_cmd, timeout=15)
        if stdout.read().decode(errors='replace').strip() != 'DIR_OK':
            raise Exception(f"Directory not found on server: {remote_path}")
        patterns_str = ' '.join(f"*{p}*" for p in date_patterns)
        cmd = (
            f"cd '{remote_path}' && "
            f"ls -1 {patterns_str} 2>/dev/null | sort -u | "
            "while IFS= read -r f; do "
            "printf '%s\\t%s\\t%s\\t%s\\n' \"$f\" \"$(stat -c%s -- \"$f\" 2>/dev/null)\" \"$(wc -l < \"$f\" 2>/dev/null)\" \"$(stat -c%Y -- \"$f\" 2>/dev/null)\"; "
            "done"
        )
        _, stdout, _ = ssh.exec_command(cmd, timeout=45)
        raw = stdout.read().decode(errors='replace')
        files = {}
        for line in raw.splitlines():
            parts = line.split('\t')
            if len(parts) == 4 and parts[1].strip().isdigit() and parts[2].strip().isdigit():
                mtime_str = None
                if parts[3].strip().isdigit():
                    dt = datetime.fromtimestamp(int(parts[3].strip()), tz=IST)
                    mtime_str = dt.strftime('%I:%M %p')
                files[parts[0]] = {'size': int(parts[1]), 'count': int(parts[2]), 'modified': mtime_str}
        return files
    finally:
        ssh.close()


def _run_outgoing_qc_check(remote_path, expected, date_patterns, check_date):
    """
    expected: list of (filename, min_count, max_count)
    date_patterns: list of date strings to glob
    Statuses: ok, missing, low (below min), high (above max), unexpected
    """
    actual_files = _list_remote_files_with_patterns(remote_path, date_patterns)
    expected_names = {name for name, _, _ in expected}
    rows = []
    issue_count = 0

    for name, min_c, max_c in expected:
        if name in actual_files:
            info = actual_files[name]
            count = info['count']
            mod = info.get('modified')
            if count < min_c:
                rows.append({'name': name, 'size': info['size'], 'count': count, 'modified': mod,
                             'status': 'low', 'min': min_c, 'max': max_c})
                issue_count += 1
            elif count > max_c:
                rows.append({'name': name, 'size': info['size'], 'count': count, 'modified': mod,
                             'status': 'high', 'min': min_c, 'max': max_c})
                issue_count += 1
            else:
                rows.append({'name': name, 'size': info['size'], 'count': count, 'modified': mod,
                             'status': 'ok'})
        else:
            rows.append({'name': name, 'size': None, 'count': None, 'modified': None,
                         'status': 'missing', 'min': min_c, 'max': max_c})
            issue_count += 1

    for name, info in actual_files.items():
        if name not in expected_names:
            rows.append({'name': name, 'size': info['size'], 'count': info['count'],
                         'modified': info.get('modified'), 'status': 'unexpected'})
            issue_count += 1

    rows.sort(key=lambda r: r['name'])
    return {
        'check_date': check_date.strftime('%Y%m%d'),
        'path': remote_path,
        'rows': rows,
        'issue_count': issue_count
    }


@app.route('/api/qc/outgoing-liveramp')
def api_qc_outgoing_liveramp():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    d = _ist_today()
    ymd = d.strftime('%Y%m%d')
    expected = [(f'liveramp_crm_daily_{ymd}.csv', 2000, 8000)]
    try:
        result = _run_outgoing_qc_check(LIVERAMP_PATH, expected, [ymd], d)
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500
    return jsonify(result)


@app.route('/api/qc/outgoing-rewards')
def api_qc_outgoing_rewards():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    d = _ist_today()
    ymd = d.strftime('%Y%m%d')
    expected = [(f'RewardsFile_EAtoLP_{ymd}.txt', 20, 500)]
    try:
        result = _run_outgoing_qc_check(REWARDS_PATH, expected, [ymd], d)
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500
    return jsonify(result)


@app.route('/api/qc/outgoing-pebblepost')
def api_qc_outgoing_pebblepost():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    d = _ist_today()
    ymd_h = d.strftime('%Y-%m-%d')   # hyphenated: 2026-06-17
    expected = [
        (f'1309_pebblepost_Blocklist_{ymd_h}.csv',    100,  600),
        (f'1309_pebblepost_ccpa_{ymd_h}.csv',           1,   50),
        (f'1309_pebblepost_Customer_{ymd_h}.csv',     100, 5000),
        (f'1309_pebblepost_transaction_{ymd_h}.csv',  100, 6000),
    ]
    try:
        result = _run_outgoing_qc_check(PEBBLEPOST_PATH, expected, [ymd_h], d)
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500
    return jsonify(result)


@app.route('/api/qc/outgoing-ga-hourly')
def api_qc_outgoing_ga_hourly():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    d = _ist_today()
    ymd = d.strftime('%Y%m%d')
    try:
        ssh = _ssh_connect()
        check_cmd = f"test -d '{GA_HOURLY_PATH}' && echo DIR_OK || echo DIR_MISSING"
        _, stdout, _ = ssh.exec_command(check_cmd, timeout=15)
        if stdout.read().decode(errors='replace').strip() != 'DIR_OK':
            ssh.close()
            raise Exception(f"Directory not found: {GA_HOURLY_PATH}")
        cmd = (
            f"cd '{GA_HOURLY_PATH}' && ls -1 *{ymd}* 2>/dev/null | sort | "
            "while IFS= read -r f; do "
            "printf '%s\\t%s\\n' \"$f\" \"$(stat -c%Y -- \"$f\" 2>/dev/null)\"; "
            "done"
        )
        _, stdout, _ = ssh.exec_command(cmd, timeout=30)
        raw = stdout.read().decode(errors='replace').strip()
        ssh.close()
        files = []
        latest_mtime = None
        for line in raw.splitlines():
            parts = line.split('\t')
            if parts and parts[0].strip():
                files.append(parts[0].strip())
                if len(parts) == 2 and parts[1].strip().isdigit():
                    mt = int(parts[1].strip())
                    if latest_mtime is None or mt > latest_mtime:
                        latest_mtime = mt
        latest_modified = None
        if latest_mtime:
            dt = datetime.fromtimestamp(latest_mtime, tz=IST)
            latest_modified = dt.strftime('%I:%M %p')
        return jsonify({
            'check_date': d.strftime('%Y%m%d'),
            'file_count': len(files),
            'files': files,
            'latest_modified': latest_modified,
            'path': GA_HOURLY_PATH
        })
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500


@app.route('/api/qc/outgoing-criteo')
def api_qc_outgoing_criteo():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    d = _ist_today()
    ymd = d.strftime('%Y%m%d')
    expected = [(f'lp_offline{ymd}.csv', 80, 700)]
    try:
        result = _run_outgoing_qc_check(CRITEO_PATH, expected, [ymd], d)
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500
    return jsonify(result)


# ==================== DAILY QC MONITOR: EA VENDOR EXCHANGE ====================

@app.route('/api/qc/vendor-cdi')
def api_qc_vendor_cdi():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    d = _ist_yesterday()
    yymmdd = d.strftime('%y%m%d')   # e.g. 260707
    sent_name   = f'ZAUTOZ.LP7.M3130203.DEA{yymmdd}'
    return_name = f'LP7_CDI_Daily_DEA{yymmdd}.csv'
    try:
        sent_files   = _list_remote_files_with_patterns(CDI_ARCHIVE_PATH, [yymmdd])
        return_files = _list_remote_files_with_patterns(CDI_RETURN_PATH,  [yymmdd])
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500

    issue_count = 0

    if sent_name in sent_files:
        info = sent_files[sent_name]
        sent_count = info['count']
        if sent_count < 1000:
            sent_status = 'low'
            issue_count += 1
        elif sent_count > 30000:
            sent_status = 'high'
            issue_count += 1
        else:
            sent_status = 'ok'
        sent_row = {'name': sent_name, 'count': sent_count, 'size': info['size'],
                    'modified': info.get('modified'), 'status': sent_status, 'min': 1000, 'max': 30000}
    else:
        sent_count = None
        sent_row = {'name': sent_name, 'count': None, 'size': None, 'modified': None,
                    'status': 'missing', 'min': 1000, 'max': 30000}
        issue_count += 1

    if return_name in return_files:
        info = return_files[return_name]
        ret_count = info['count']
        if sent_count is not None:
            min_return = int(sent_count * 0.90)  # allow up to 10% drop
            if ret_count > sent_count:
                ret_status = 'high'   # received more than sent
                issue_count += 1
            elif ret_count < min_return:
                ret_status = 'low'    # dropped more than 10%
                issue_count += 1
            else:
                ret_status = 'ok'
        else:
            ret_status = 'ok'  # can't validate without sent count
        min_ret = int(sent_count * 0.90) if sent_count is not None else None
        ret_row = {'name': return_name, 'count': ret_count, 'size': info['size'],
                   'modified': info.get('modified'), 'status': ret_status,
                   'sent_count': sent_count, 'min_return': min_ret}
    else:
        ret_row = {'name': return_name, 'count': None, 'size': None, 'modified': None,
                   'status': 'missing', 'sent_count': sent_count}
        issue_count += 1

    return jsonify({'check_date': yymmdd, 'sent': sent_row, 'return': ret_row,
                    'issue_count': issue_count})


@app.route('/api/qc/vendor-briteverify')
def api_qc_vendor_briteverify():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    d = _ist_yesterday()
    ymd = d.strftime('%Y%m%d')   # e.g. 20260707
    file_name = f'final_output_briteverify_{ymd}.csv'
    try:
        files = _list_remote_files_with_patterns(BRITEVERIFY_PATH, [ymd])
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500

    issue_count = 0
    if file_name in files:
        info = files[file_name]
        file_row = {'name': file_name, 'count': info['count'], 'size': info['size'],
                    'modified': info.get('modified'), 'status': 'ok'}
    else:
        file_row = {'name': file_name, 'count': None, 'size': None, 'modified': None,
                    'status': 'missing'}
        issue_count += 1

    return jsonify({'check_date': ymd, 'file': file_row, 'issue_count': issue_count})


@app.route('/api/qc/vendor-oracle')
def api_qc_vendor_oracle():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    d = _ist_today()
    ymd = d.strftime('%Y%m%d')   # e.g. 20260708
    expected_files = [
        f'Deliverable_touch_file_{ymd}',
        f'DMS_DIM_FACT_{ymd}',
    ]
    try:
        files = _list_remote_files_with_patterns(ORACLE_FILEWATCH_PATH, [ymd])
    except Exception as e:
        return jsonify({'error': f'Connection Error: {str(e)}'}), 500

    issue_count = 0
    rows = []
    for fname in expected_files:
        if fname in files:
            info = files[fname]
            rows.append({'name': fname, 'modified': info.get('modified'), 'status': 'ok'})
        else:
            rows.append({'name': fname, 'modified': None, 'status': 'missing'})
            issue_count += 1

    return jsonify({'check_date': ymd, 'rows': rows, 'issue_count': issue_count})


# ==================== EA TODAY EVENT (REDSHIFT CAMPAIGN CALENDAR) ====================

@app.route('/api/today-events')
def api_today_events():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    today = datetime.now(IST).date()
    week_start = today - timedelta(days=today.weekday())   # Monday
    week_end   = week_start + timedelta(days=6)            # Sunday

    # Single scan: pull all date columns for rows that have at least one match
    or_clause = ' OR '.join(
        f"{col}::date BETWEEN %(ws)s AND %(we)s"
        for col in CAMPAIGN_DATE_COLS
    )
    col_list = ', '.join(CAMPAIGN_DATE_COLS)
    query = (
        f"SELECT campaign_name, {col_list} "
        f"FROM LPDATAMART.tbl_d_campaign "
        f"WHERE {or_clause}"
    )

    try:
        conn = _redshift_connect()
        cur = conn.cursor()
        cur.execute(query, {'ws': week_start, 'we': week_end})
        raw_rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    events = []
    for row in raw_rows:
        campaign_name = row[0] or '—'
        for i, col in enumerate(CAMPAIGN_DATE_COLS):
            val = row[i + 1]
            if val is None:
                continue
            # val may be datetime.date or datetime.datetime
            event_date = val.date() if isinstance(val, datetime) else val
            if week_start <= event_date <= week_end:
                events.append({
                    'event_type':    col,
                    'event_date':    event_date.strftime('%Y-%m-%d'),
                    'campaign_name': campaign_name,
                    'is_today':      event_date == today,
                })

    events.sort(key=lambda e: (e['event_date'], e['event_type'], e['campaign_name']))

    return jsonify({
        'week_start': week_start.strftime('%Y-%m-%d'),
        'week_end':   week_end.strftime('%Y-%m-%d'),
        'today':      today.strftime('%Y-%m-%d'),
        'events':     events,
        'count':      len(events),
    })


# ==================== GMAIL SERVICE ACCOUNT & SEND TO LP ====================

GMAIL_SCOPES       = ['https://www.googleapis.com/auth/gmail.send']
GMAIL_SA_FILE      = os.environ.get('GMAIL_SA_FILE', 'service_account.json')
GMAIL_SENDER       = 'kiran@expressanalytics.net'   # send AS this address (domain-wide delegation)
LP_EMAIL_TO        = 'kiran@expressanalytics.net'

_EVENT_LABELS_PY = {
    'campaign_start_date':      'Campaign Start',
    'campaign_end_date':        'Campaign End',
    'campaign_drop_date':       'Campaign Drop',
    'cdi_full_refresh_send':    'CDI Full Refresh Send',
    'cdi_full_refresh_receive': 'CDI Full Refresh Receive',
    'modeling_kick_off':        'Modeling Kick-off',
    'final_model_due':          'Final Model Due',
    'lp_score_approval':        'LP Score Approval',
    'final_scoring':            'Final Scoring',
    'lp_count_approval':        'LP Count Approval',
    'ea_merge_purge_delivery':  'EA Merge/Purge Delivery',
    'cumm_cell':                'Cumm Cell',
    'circ_plan':                'Circ Plan',
    'mail_file':                'Mail File',
    'hygine_files':             'Hygiene Files',
    'ntf_estimate_rank_score':  'NTF Estimate Rank Score',
    'ntf_actual_rank_score':    'NTF Actual Rank Score',
    'ea_ntf_merge_purge_delvr': 'EA NTF Merge/Purge Delivery',
    'ntf_mail_file':            'NTF Mail File',
    'ntf_hygine_files':         'NTF Hygiene Files',
    'ntf_cumm_cell':            'NTF Cumm Cell',
    'gross_in':                 'Gross In',
    'campaign_ntf_start_date':  'Campaign NTF Start',
}


def _gmail_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    # Load service account JSON from env var (Vercel) or local file
    sa_json = os.environ.get('GMAIL_SERVICE_ACCOUNT_JSON')
    if sa_json:
        sa_info = json.loads(sa_json)
    elif os.path.exists(GMAIL_SA_FILE):
        with open(GMAIL_SA_FILE) as f:
            sa_info = json.load(f)
    else:
        return None

    try:
        creds = service_account.Credentials.from_service_account_info(
            sa_info, scopes=GMAIL_SCOPES
        ).with_subject(GMAIL_SENDER)
        return build('gmail', 'v1', credentials=creds)
    except Exception:
        return None


def _build_lp_email_html(qc_summary, events, week_label):
    today_str  = datetime.now(IST).strftime('%B %d, %Y')
    total_fail = sum(1 for r in qc_summary if not (r.get('pass') and not r.get('error')))
    banner_bg  = '#fef2f2' if total_fail else '#d1fae5'
    banner_bc  = '#fca5a5' if total_fail else '#6ee7b7'
    banner_col = '#991b1b' if total_fail else '#065f46'
    banner_txt = f'{total_fail} feed{"s" if total_fail != 1 else ""} with issues' if total_fail else 'All feeds OK'

    # ---- QC rows ----
    qc_rows = ''
    for r in qc_summary:
        name   = r.get('name', '—')
        is_err = r.get('error', False)
        is_ok  = r.get('pass', False) and not is_err
        n_iss  = r.get('issue_count', 0)
        if is_ok:
            badge = '<span style="color:#059669;font-weight:700;">&#10003; PASS</span>'
            row_s = ''
        elif is_err:
            badge = '<span style="color:#92400e;font-weight:700;">&#9888; ERROR</span>'
            row_s = 'background:#fffbeb;'
        else:
            badge = '<span style="color:#dc2626;font-weight:700;">&#10007; FAIL</span>'
            row_s = 'background:#fef2f2;'
        qc_rows += (
            f'<tr style="{row_s}">'
            f'<td style="padding:7px 14px;border-bottom:1px solid #e5e7eb;">{name}</td>'
            f'<td style="padding:7px 14px;border-bottom:1px solid #e5e7eb;text-align:center;">{badge}</td>'
            f'<td style="padding:7px 14px;border-bottom:1px solid #e5e7eb;text-align:center;color:#6b7280;">'
            f'{"&mdash;" if is_ok or is_err else n_iss}</td>'
            f'</tr>'
        )
        # Issue detail rows
        rows = r.get('rows', [])
        for row in rows:
            st = row.get('status', '')
            if st == 'ok':
                continue
            st_label = {'missing': 'MISSING', 'low': 'LOW COUNT', 'high': 'HIGH COUNT',
                        'flagged': 'FLAGGED', 'unexpected': 'UNEXPECTED'}.get(st, st.upper())
            cnt = row.get('count')
            cnt_str = f'{cnt:,}' if isinstance(cnt, int) else '&mdash;'
            qc_rows += (
                f'<tr style="background:#fafafa;">'
                f'<td style="padding:5px 14px 5px 28px;border-bottom:1px solid #f3f4f6;'
                f'font-family:monospace;font-size:12px;color:#374151;" colspan="2">'
                f'&#8627; {row.get("name","")}</td>'
                f'<td style="padding:5px 14px;border-bottom:1px solid #f3f4f6;'
                f'font-size:12px;color:#dc2626;">{st_label} ({cnt_str})</td>'
                f'</tr>'
            )

    # ---- Event rows ----
    ev_rows = ''
    if events:
        for ev in events:
            is_today = ev.get('is_today', False)
            row_s = 'background:#eff6ff;' if is_today else ''
            label = _EVENT_LABELS_PY.get(ev.get('event_type', ''), ev.get('event_type', ''))
            today_tag = ' <b style="color:#1d4ed8;">[TODAY]</b>' if is_today else ''
            ev_rows += (
                f'<tr style="{row_s}">'
                f'<td style="padding:6px 14px;border-bottom:1px solid #e5e7eb;white-space:nowrap;">'
                f'{ev.get("event_date","")}{today_tag}</td>'
                f'<td style="padding:6px 14px;border-bottom:1px solid #e5e7eb;">{label}</td>'
                f'<td style="padding:6px 14px;border-bottom:1px solid #e5e7eb;color:#6b7280;">'
                f'{ev.get("campaign_name","&mdash;")}</td>'
                f'</tr>'
            )
    else:
        ev_rows = ('<tr><td colspan="3" style="padding:14px;color:#9ca3af;text-align:center;">'
                   'No campaign events this week</td></tr>')

    return f"""
<html><body style="font-family:Arial,Helvetica,sans-serif;color:#111827;max-width:720px;
margin:0 auto;padding:24px 16px;background:#f9fafb;">
<div style="background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 1px 4px rgba(0,0,0,.07);">
  <h2 style="margin:0 0 4px;font-size:20px;color:#111827;">LP One Platform &mdash; Daily QC Report</h2>
  <p style="margin:0 0 20px;color:#6b7280;font-size:13px;">
    Date: <strong>{today_str}</strong> &nbsp;|&nbsp; Sent to: <strong>{LP_EMAIL_TO}</strong>
  </p>

  <div style="background:{banner_bg};border:1px solid {banner_bc};border-radius:8px;
       padding:14px 18px;margin-bottom:24px;">
    <strong style="font-size:15px;color:{banner_col};">{banner_txt}</strong>
  </div>

  <h3 style="font-size:14px;text-transform:uppercase;letter-spacing:.05em;
      color:#6b7280;margin:0 0 10px;">QC Results</h3>
  <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;
         border-radius:8px;overflow:hidden;margin-bottom:28px;">
    <thead><tr style="background:#f3f4f6;">
      <th style="text-align:left;padding:9px 14px;font-size:12px;color:#374151;
          text-transform:uppercase;letter-spacing:.04em;">Feed</th>
      <th style="text-align:center;padding:9px 14px;font-size:12px;color:#374151;
          text-transform:uppercase;letter-spacing:.04em;">Status</th>
      <th style="text-align:center;padding:9px 14px;font-size:12px;color:#374151;
          text-transform:uppercase;letter-spacing:.04em;">Issues</th>
    </tr></thead>
    <tbody>{qc_rows}</tbody>
  </table>

  <h3 style="font-size:14px;text-transform:uppercase;letter-spacing:.05em;
      color:#6b7280;margin:0 0 6px;">Campaign Events &mdash; Week of {week_label}</h3>
  <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;
         border-radius:8px;overflow:hidden;margin-bottom:28px;">
    <thead><tr style="background:#f3f4f6;">
      <th style="text-align:left;padding:9px 14px;font-size:12px;color:#374151;
          text-transform:uppercase;letter-spacing:.04em;">Date</th>
      <th style="text-align:left;padding:9px 14px;font-size:12px;color:#374151;
          text-transform:uppercase;letter-spacing:.04em;">Event</th>
      <th style="text-align:left;padding:9px 14px;font-size:12px;color:#374151;
          text-transform:uppercase;letter-spacing:.04em;">Campaign</th>
    </tr></thead>
    <tbody>{ev_rows}</tbody>
  </table>

  <p style="margin:0;font-size:11px;color:#9ca3af;border-top:1px solid #f3f4f6;
     padding-top:14px;">Generated by LP One Platform &mdash; Express Analytics</p>
</div>
</body></html>"""


@app.route('/auth/gmail/status')
def auth_gmail_status():
    if 'logged_in' not in session:
        return jsonify({'authorized': False}), 401
    svc = _gmail_service()
    return jsonify({'authorized': svc is not None})


@app.route('/api/send-lp-email', methods=['POST'])
def api_send_lp_email():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    svc = _gmail_service()
    if not svc:
        return jsonify({'error': 'Gmail not authorized', 'needs_auth': True}), 403

    payload     = request.get_json() or {}
    qc_summary  = payload.get('qc_summary', [])
    events      = payload.get('events', [])
    week_label  = payload.get('week_label', '')

    html_body = _build_lp_email_html(qc_summary, events, week_label)

    from email.mime.multipart import MIMEMultipart as _MMP
    from email.mime.text import MIMEText as _MT
    import base64 as _b64

    today_str = datetime.now(IST).strftime('%b %d, %Y')
    total_fail = sum(1 for r in qc_summary if not (r.get('pass') and not r.get('error')))
    prefix = '❌' if total_fail else '✅'

    msg = _MMP('alternative')
    msg['To']      = LP_EMAIL_TO
    msg['From']    = GMAIL_SENDER
    msg['Subject'] = f'{prefix} LP QC Report – {today_str}'
    msg.attach(_MT(html_body, 'html'))

    raw = _b64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        svc.users().messages().send(userId='me', body={'raw': raw}).execute()
        return jsonify({'ok': True, 'message': f'Email sent to {LP_EMAIL_TO}'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== QC SUMMARY EMAIL ====================
# Configure these before using the Send Email feature
QC_EMAIL_TO   = ''          # e.g. 'lp-team@lampsplus.com'
QC_EMAIL_FROM = ''          # e.g. 'ea-alerts@expressanalytics.com'
QC_SMTP_HOST  = 'smtp.gmail.com'
QC_SMTP_PORT  = 587
QC_SMTP_USER  = ''          # SMTP login (usually same as QC_EMAIL_FROM)
QC_SMTP_PASS  = ''          # SMTP password or app password


@app.route('/api/qc/send-summary-email', methods=['POST'])
def api_qc_send_summary_email():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    if not QC_EMAIL_TO or not QC_EMAIL_FROM or not QC_SMTP_USER or not QC_SMTP_PASS:
        return jsonify({'error': 'Email not configured. Please set QC_EMAIL_TO, QC_EMAIL_FROM, QC_SMTP_USER, QC_SMTP_PASS in app.py.'}), 500

    payload = request.get_json()
    check_date    = payload.get('check_date', '—')
    total_issues  = payload.get('total_issues', 0)
    feeds         = payload.get('feeds', [])

    status_icon = '❌' if total_issues > 0 else '✅'
    subject = f"{status_icon} EA Daily QC – {total_issues} issue{'s' if total_issues != 1 else ''} found – {check_date}"

    rows_html = ''
    for feed in feeds:
        if feed['issue_count'] == 0:
            rows_html += f'<tr><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;">{feed["name"]}</td><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#059669;font-weight:600;">✅ All OK</td></tr>'
        else:
            rows_html += f'<tr><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-weight:600;">{feed["name"]}</td><td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;color:#dc2626;font-weight:600;">❌ {feed["issue_count"]} issue{"s" if feed["issue_count"] != 1 else ""}</td></tr>'
            for issue in feed.get('issues', []):
                status_label = {'missing': 'MISSING', 'flagged': 'LOW COUNT', 'unexpected': 'UNEXPECTED'}.get(issue['status'], issue['status'].upper())
                count_str = str(issue['count']) if issue['count'] is not None else '—'
                rows_html += f'<tr style="background:#fafafa;"><td style="padding:6px 12px 6px 28px;border-bottom:1px solid #f3f4f6;font-family:monospace;font-size:13px;color:#374151;">↳ {issue["name"]}</td><td style="padding:6px 12px;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;">{status_label} (count: {count_str})</td></tr>'

    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#111827;max-width:700px;margin:0 auto;padding:24px;">
      <h2 style="margin-bottom:4px;">EA Daily QC Monitor</h2>
      <p style="color:#6b7280;margin-top:0;">Check date: <strong>{check_date}</strong> &nbsp;|&nbsp; Run by: <strong>directmarketing</strong></p>
      <div style="background:{'#fef2f2' if total_issues > 0 else '#d1fae5'};border:1px solid {'#fca5a5' if total_issues > 0 else '#6ee7b7'};border-radius:8px;padding:16px 20px;margin:16px 0;">
        <strong style="font-size:18px;color:{'#991b1b' if total_issues > 0 else '#065f46'};">{total_issues} total issue{'s' if total_issues != 1 else ''} found across {len(feeds)} feed{'s' if len(feeds) != 1 else ''}</strong>
      </div>
      <table style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
        <thead><tr style="background:#f9fafb;">
          <th style="text-align:left;padding:10px 12px;color:#374151;font-size:14px;">Feed</th>
          <th style="text-align:left;padding:10px 12px;color:#374151;font-size:14px;">Status</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style="color:#9ca3af;font-size:12px;margin-top:24px;">Sent from LP One Platform – EA Daily QC Monitor</p>
    </body></html>
    """

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = QC_EMAIL_FROM
        msg['To']      = QC_EMAIL_TO
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP(QC_SMTP_HOST, QC_SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(QC_SMTP_USER, QC_SMTP_PASS)
            smtp.sendmail(QC_EMAIL_FROM, QC_EMAIL_TO, msg.as_string())
        return jsonify({'success': True, 'to': QC_EMAIL_TO})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== QC HISTORY LOG ====================

CRON_SECRET    = os.environ.get('CRON_SECRET', '')
QC_LOG_TABLE   = 'reports.tbl_qc_log'


def _ensure_qc_log_table():
    ddl = f"""
        CREATE TABLE IF NOT EXISTS {QC_LOG_TABLE} (
            run_id      VARCHAR(30)   NOT NULL,
            run_time    TIMESTAMP     NOT NULL,
            run_date    DATE          NOT NULL,
            run_type    VARCHAR(20)   NOT NULL,
            feed_name   VARCHAR(100)  NOT NULL,
            feed_key    VARCHAR(60)   NOT NULL,
            status      VARCHAR(10)   NOT NULL,
            issue_count INT           NOT NULL DEFAULT 0,
            check_date  VARCHAR(20),
            email_sent  BOOL          DEFAULT FALSE,
            issues_json VARCHAR(4000)
        )
        DISTSTYLE EVEN
        SORTKEY(run_date)
    """
    conn = _redshift_connect()
    cur  = conn.cursor()
    cur.execute(ddl)
    conn.commit()
    cur.close()
    conn.close()


def _insert_qc_run(run_id, run_type, results, email_sent=False):
    _ensure_qc_log_table()
    run_time = datetime.now(IST).replace(tzinfo=None)
    run_date = run_time.date()
    rows = []
    for r in results:
        rows.append((
            run_id, run_time, run_date, run_type,
            r.get('name', ''), r.get('key', ''),
            r.get('status', 'error'), r.get('issue_count', 0),
            r.get('check_date'), email_sent,
            json.dumps(r.get('issues', []))[:4000],
        ))
    conn = _redshift_connect()
    cur  = conn.cursor()
    cur.executemany(
        f"""INSERT INTO {QC_LOG_TABLE}
            (run_id,run_time,run_date,run_type,feed_name,feed_key,
             status,issue_count,check_date,email_sent,issues_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        rows,
    )
    conn.commit()
    cur.close()
    conn.close()


@app.route('/api/log-qc-run', methods=['POST'])
def api_log_qc_run():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    payload  = request.get_json() or {}
    run_id   = payload.get('run_id') or datetime.now(IST).strftime('%Y%m%d_%H%M%S')
    run_type = payload.get('run_type', 'manual')
    results  = payload.get('results', [])
    try:
        _insert_qc_run(run_id, run_type, results, email_sent=False)
        return jsonify({'ok': True, 'run_id': run_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _run_all_qc_internal():
    """Call all 11 QC checks via Flask test client; return normalised results list."""
    feed_map = [
        ('Web Data Files',              'web_data_files',   '/api/qc/web-data-files'),
        ('POS Data Files',              'pos_data_files',   '/api/qc/pos-data-files'),
        ('Bluecore Email',              'email_bluecore',   '/api/qc/email-bluecore'),
        ('Wunderkind Email & SMS',      'email_wunderkind', '/api/qc/email-wunderkind'),
        ('LiveRamp CRM',                'out_liveramp',     '/api/qc/outgoing-liveramp'),
        ('Reward Assignment',           'out_rewards',      '/api/qc/outgoing-rewards'),
        ('Pebble Post',                 'out_pebblepost',   '/api/qc/outgoing-pebblepost'),
        ('GA Hourly → Bluecore',  'out_ga_hourly',    '/api/qc/outgoing-ga-hourly'),
        ('Criteo',                      'out_criteo',       '/api/qc/outgoing-criteo'),
        ('CDI → Experian Exchange','vendor_cdi',       '/api/qc/vendor-cdi'),
        ('BriteVerify Validation',      'vendor_brite',     '/api/qc/vendor-briteverify'),
        ('Oracle Sync to Redshift',     'vendor_oracle',    '/api/qc/vendor-oracle'),
    ]
    results = []
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess['logged_in'] = True
        for name, key, endpoint in feed_map:
            try:
                resp = client.get(endpoint)
                data = resp.get_json() or {}
                if data.get('error'):
                    results.append({'name': name, 'key': key, 'status': 'error',
                                    'issue_count': 0, 'check_date': None, 'issues': []})
                else:
                    issue_count = data.get('issue_count', 0)
                    check_date  = data.get('check_date')
                    if key == 'out_ga_hourly':
                        issue_count = 0 if data.get('file_count', 0) > 0 else 1
                        issues = [] if issue_count == 0 else [{'name': 'No hourly files', 'status': 'missing', 'count': 0}]
                    elif 'rows' in data:
                        issues = [r for r in data['rows'] if r.get('status') != 'ok']
                    elif 'file' in data:
                        f = data['file']
                        issues = [f] if f.get('status') != 'ok' else []
                    elif 'sent' in data:
                        issues = [r for r in [data.get('sent'), data.get('return')]
                                  if r and r.get('status') != 'ok']
                    else:
                        issues = []
                    results.append({'name': name, 'key': key,
                                    'status': 'pass' if issue_count == 0 else 'fail',
                                    'issue_count': issue_count,
                                    'check_date': check_date, 'issues': issues})
            except Exception as exc:
                results.append({'name': name, 'key': key, 'status': 'error',
                                'issue_count': 0, 'check_date': None,
                                'issues': [{'name': str(exc), 'status': 'error', 'count': None}]})
    return results


@app.route('/api/run-scheduled-qc')
def api_run_scheduled_qc():
    auth   = request.headers.get('Authorization', '')
    secret = os.environ.get('CRON_SECRET', '')
    if secret and auth != f'Bearer {secret}':
        return jsonify({'error': 'Forbidden'}), 403

    try:
        results  = _run_all_qc_internal()
        run_id   = datetime.now(IST).strftime('%Y%m%d_%H%M%S')

        # Send LP email
        email_sent = False
        try:
            svc = _gmail_service()
            if svc:
                qc_summary = [{'name': r['name'],
                               'pass':  r['status'] == 'pass',
                               'error': r['status'] == 'error',
                               'issue_count': r['issue_count'],
                               'rows':  r.get('issues', [])} for r in results]
                today      = datetime.now(IST).date()
                week_start = today - timedelta(days=today.weekday())
                week_end   = week_start + timedelta(days=6)
                week_label = (f"{week_start.strftime('%b %d')} – "
                              f"{week_end.strftime('%b %d, %Y')}")
                events = []
                try:
                    or_clause = ' OR '.join(
                        f"{col}::date BETWEEN %(ws)s AND %(we)s" for col in CAMPAIGN_DATE_COLS)
                    col_list = ', '.join(CAMPAIGN_DATE_COLS)
                    query = (f"SELECT campaign_name, {col_list} "
                             f"FROM LPDATAMART.tbl_d_campaign WHERE {or_clause}")
                    conn = _redshift_connect()
                    cur  = conn.cursor()
                    cur.execute(query, {'ws': week_start, 'we': week_end})
                    for row in cur.fetchall():
                        campaign_name = row[0] or '—'
                        for i, col in enumerate(CAMPAIGN_DATE_COLS):
                            val = row[i + 1]
                            if val is None:
                                continue
                            event_date = val.date() if isinstance(val, datetime) else val
                            if week_start <= event_date <= week_end:
                                events.append({'event_type': col,
                                               'event_date': event_date.strftime('%Y-%m-%d'),
                                               'campaign_name': campaign_name,
                                               'is_today': event_date == today})
                    cur.close()
                    conn.close()
                    events.sort(key=lambda e: (e['event_date'], e['event_type']))
                except Exception:
                    pass

                html_body = _build_lp_email_html(qc_summary, events, week_label)
                from email.mime.multipart import MIMEMultipart as _MMP
                from email.mime.text import MIMEText as _MT
                import base64 as _b64
                today_str  = datetime.now(IST).strftime('%b %d, %Y')
                total_fail = sum(1 for r in qc_summary
                                 if not (r.get('pass') and not r.get('error')))
                prefix_icon = '❌' if total_fail else '✅'
                msg = _MMP('alternative')
                msg['To']      = LP_EMAIL_TO
                msg['From']    = GMAIL_SENDER
                msg['Subject'] = f'{prefix_icon} LP QC Report – {today_str}'
                msg.attach(_MT(html_body, 'html'))
                raw = _b64.urlsafe_b64encode(msg.as_bytes()).decode()
                svc.users().messages().send(userId='me', body={'raw': raw}).execute()
                email_sent = True
        except Exception:
            pass

        _insert_qc_run(run_id, 'scheduled', results, email_sent=email_sent)
        total_issues = sum(r['issue_count'] for r in results)
        return jsonify({'ok': True, 'run_id': run_id,
                        'total_issues': total_issues,
                        'feeds_checked': len(results),
                        'email_sent': email_sent})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/qc-history')
def api_qc_history():
    if 'logged_in' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    try:
        _ensure_qc_log_table()
        conn = _redshift_connect()
        cur  = conn.cursor()
        cur.execute(f"""
            SELECT run_id, run_date, run_time, run_type,
                   feed_name, feed_key, status, issue_count,
                   check_date, email_sent
            FROM {QC_LOG_TABLE}
            WHERE run_date >= CURRENT_DATE - 30
            ORDER BY run_time DESC, feed_name
            LIMIT 600
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    from collections import OrderedDict
    runs = OrderedDict()
    for (run_id, run_date, run_time, run_type,
         feed_name, feed_key, status, issue_count,
         check_date, email_sent) in rows:
        if run_id not in runs:
            rd = run_date.strftime('%Y-%m-%d') if hasattr(run_date, 'strftime') else str(run_date)
            rt = run_time.strftime('%Y-%m-%d %H:%M:%S') if hasattr(run_time, 'strftime') else str(run_time)
            runs[run_id] = {
                'run_id': run_id, 'run_date': rd, 'run_time': rt,
                'run_type': run_type, 'total_issues': 0,
                'feeds_checked': 0, 'feeds_passed': 0,
                'email_sent': bool(email_sent), 'feeds': [],
            }
        runs[run_id]['feeds'].append({
            'name': feed_name, 'key': feed_key,
            'status': status, 'issue_count': issue_count,
            'check_date': check_date,
        })
        runs[run_id]['total_issues']  += issue_count
        runs[run_id]['feeds_checked'] += 1
        if status == 'pass':
            runs[run_id]['feeds_passed'] += 1

    return jsonify({'runs': list(runs.values())})


# Set response headers for security
@app.after_request
def add_security_headers(response):
    """Add security headers to all responses"""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8050)
