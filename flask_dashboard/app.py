from flask import Flask, render_template, jsonify, abort
import firebase_admin
from firebase_admin import credentials, db
from jinja2.exceptions import TemplateNotFound

app = Flask(__name__)

# Firebase Admin SDK 초기화
try:
    cred = credentials.Certificate("/home/craft/can_logger/serviceAccountKey.json")
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://emucanlogger-default-rtdb.firebaseio.com/'
    })
    db_ref = db.reference('emu_realtime_data')
except Exception as e:
    print(f"Firebase 초기화 실패: {e}")
    db_ref = None

@app.route("/")
def home():
    """홈페이지 (index.html) 를 렌더링합니다."""
    return render_template("index.html")

@app.route("/dashboard")
def dashboard():
    """대시보드 페이지 (dashboard.html) 를 렌더링합니다."""
    return render_template("dashboard.html")

@app.route("/graph")
def graph():
    """그래프 페이지 (graph.html) 를 렌더링합니다."""
    return render_template("graph.html")

@app.route("/adu")
def adu():
    """ADU 페이지 (adu.html) 를 렌더링합니다."""
    return render_template("adu.html")

@app.route("/<page_name>")
def show_page(page_name):
    """요청된 이름의 HTML 페이지를 동적으로 렌더링합니다."""
    try:
        return render_template(f"{page_name}.html")
    except TemplateNotFound:
        abort(404)

@app.route("/data")
def data():
    """Firebase에서 실시간 데이터를 가져와 JSON으로 반환합니다."""
    if db_ref:
        try:
            snapshot = db_ref.get()
            if snapshot:
                return jsonify(snapshot)
            else:
                return jsonify({})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        return jsonify({"error": "Firebase not initialized"}), 500

@app.errorhandler(404)
def page_not_found(e):
    """404 Not Found 오류 발생 시 커스텀 404 페이지를 렌더링합니다."""
    return render_template('404.html'), 404

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001, debug=True)
