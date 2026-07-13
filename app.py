from flask import Flask, render_template, redirect, url_for

app = Flask(__name__)

@app.route("/")
def home():
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", active_page="dashboard")

@app.route("/defects")
def defects():
    return render_template("defects.html", active_page="defects")

@app.route("/defects/add")
def add_defect():
    return render_template("add_defect.html", active_page="add_defect")

@app.route("/assets")
def assets():
    return render_template("assets.html", active_page="assets")

@app.route("/reports")
def reports():
    return render_template("reports.html", active_page="reports")

if __name__ == "__main__":
    app.run(debug=True)
