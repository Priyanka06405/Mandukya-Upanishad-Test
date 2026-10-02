import os
import csv
import random
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "mandukya-game-change-this")

database_url = os.environ.get("DATABASE_URL", "sqlite:///mandukya.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    personal_number = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.Text, nullable=False)
    option_b = db.Column(db.Text, nullable=False)
    option_c = db.Column(db.Text, nullable=False)
    option_d = db.Column(db.Text, nullable=False)
    correct_answer = db.Column(db.String(1), nullable=False)

class Attempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey("participant.id"), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime)
    status = db.Column(db.String(30), default="in_progress")
    score = db.Column(db.Integer, default=0)
    total_questions = db.Column(db.Integer, default=50)
    answers = db.Column(db.JSON, default=dict)
    participant = db.relationship("Participant", backref="attempts")

def sync_questions():
    with open("questions.csv", "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if Question.query.count() == len(rows):
        return
    Question.query.delete()
    for row in rows:
        db.session.add(Question(
            question_text=row["question"].strip(),
            option_a=row["option_a"].strip(),
            option_b=row["option_b"].strip(),
            option_c=row["option_c"].strip(),
            option_d=row["option_d"].strip(),
            correct_answer=row["correct_answer"].strip().upper()
        ))
    db.session.commit()

@app.route("/")
def home():
    return redirect(url_for("quiz") if session.get("attempt_id") else url_for("login"))

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name","").strip()
        personal_number = request.form.get("personal_number","").strip()
        password = request.form.get("password","")
        if not name or not personal_number or not password:
            flash("Please fill all three fields.")
            return render_template("register.html")
        if Participant.query.filter_by(personal_number=personal_number).first():
            flash("This personal number is already registered. Please login.")
            return redirect(url_for("login"))
        p = Participant(
            name=name,
            personal_number=personal_number,
            password_hash=generate_password_hash(password)
        )
        db.session.add(p)
        db.session.commit()
        session["participant_id"] = p.id
        return redirect(url_for("start"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        personal_number = request.form.get("personal_number","").strip()
        password = request.form.get("password","")
        p = Participant.query.filter_by(personal_number=personal_number).first()
        if p and check_password_hash(p.password_hash, password):
            session.clear()
            session["participant_id"] = p.id
            return redirect(url_for("start"))
        flash("Invalid personal number or password.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/start")
def start():
    pid = session.get("participant_id")
    if not pid:
        return redirect(url_for("login"))
    participant = db.session.get(Participant, pid)
    if not participant:
        return redirect(url_for("login"))
    latest = Attempt.query.filter_by(participant_id=pid).order_by(Attempt.id.desc()).first()
    if latest and latest.status == "submitted":
        return render_template("already_submitted.html", participant=participant, attempt=latest)
    return render_template("start.html", participant=participant)

@app.route("/begin")
def begin():
    pid = session.get("participant_id")
    if not pid:
        return redirect(url_for("login"))
    participant = db.session.get(Participant, pid)
    latest = Attempt.query.filter_by(participant_id=pid).order_by(Attempt.id.desc()).first()
    if latest and latest.status == "submitted":
        return redirect(url_for("start"))
    questions = Question.query.all()
    ids = [q.id for q in questions]
    random.shuffle(ids)

    answers = {"_question_ids": ids}
    letters = ["A","B","C","D"]
    for qid in ids:
        shuffled = letters[:]
        random.shuffle(shuffled)
        answers[str(qid)] = {
            "mapping": dict(zip(letters, shuffled)),
            "selected": None,
            "original": None
        }

    attempt = Attempt(
        participant_id=participant.id,
        total_questions=len(ids),
        answers=answers
    )
    db.session.add(attempt)
    db.session.commit()
    session["attempt_id"] = attempt.id
    return redirect(url_for("quiz"))

@app.route("/quiz", methods=["GET","POST"])
def quiz():
    pid = session.get("participant_id")
    aid = session.get("attempt_id")
    if not pid or not aid:
        return redirect(url_for("login"))
    participant = db.session.get(Participant, pid)
    attempt = db.session.get(Attempt, aid)
    if not participant or not attempt:
        session.clear()
        return redirect(url_for("login"))
    if attempt.status == "submitted":
        return render_template("submitted.html", participant=participant, attempt=attempt)

    stored = attempt.answers or {}
    qids = stored.get("_question_ids", [])

    if request.method == "POST":
        score = 0
        for qid in qids:
            q = db.session.get(Question, int(qid))
            info = stored.get(str(qid), {})
            mapping = info.get("mapping", {})
            selected = request.form.get(f"question_{qid}")
            original = mapping.get(selected)
            correct = original == q.correct_answer
            if correct:
                score += 1
            info["selected"] = selected
            info["original"] = original
            info["correct"] = correct
            stored[str(qid)] = info

        attempt.answers = stored
        attempt.score = score
        attempt.status = "submitted"
        attempt.submitted_at = datetime.utcnow()
        db.session.commit()
        return redirect(url_for("submitted"))

    question_data = []
    for qid in qids:
        q = db.session.get(Question, int(qid))
        info = stored.get(str(qid), {})
        mapping = info.get("mapping")
        original = {
            "A": q.option_a, "B": q.option_b,
            "C": q.option_c, "D": q.option_d
        }
        options = [
            {"letter": letter, "text": original[mapping[letter]]}
            for letter in ["A","B","C","D"]
        ]
        question_data.append({
            "number": len(question_data)+1,
            "id": q.id,
            "question": q.question_text,
            "options": options
        })

    return render_template("quiz.html", participant=participant, questions=question_data)

@app.route("/submitted")
def submitted():
    pid = session.get("participant_id")
    aid = session.get("attempt_id")
    if not pid or not aid:
        return redirect(url_for("login"))
    participant = db.session.get(Participant, pid)
    attempt = db.session.get(Attempt, aid)
    if not participant or not attempt:
        return redirect(url_for("login"))
    return render_template("submitted.html", participant=participant, attempt=attempt)

@app.route("/admin", methods=["GET","POST"])
def admin():
    if request.method == "POST":
        if request.form.get("username") == "admin" and request.form.get("password") == "admin123":
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin credentials.")
    return render_template("admin_login.html")

@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    participants = Participant.query.order_by(Participant.id.asc()).all()
    rows = []
    for p in participants:
        a = Attempt.query.filter_by(participant_id=p.id).order_by(Attempt.id.desc()).first()
        rows.append({
            "name": p.name,
            "personal_number": p.personal_number,
            "score": a.score if a and a.status == "submitted" else "-",
            "status": "Completed" if a and a.status == "submitted" else "Not attempted",
            "submitted_at": a.submitted_at if a and a.status == "submitted" else ""
        })
    return render_template("admin_dashboard.html", rows=rows)

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin"))

with app.app_context():
    db.create_all()
    sync_questions()

if __name__ == "__main__":
    app.run(debug=True)
