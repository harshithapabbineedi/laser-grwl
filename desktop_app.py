import threading
import webview
from main import app

def run_server():
    app.run(debug=False, host='127.0.0.1', port=5000)

if __name__ == '__main__':
    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    webview.create_window(
        "Laser Engraver Control",
        "http://127.0.0.1:5000",
        width=1400,
        height=900,
        resizable=True
    )

    webview.start()