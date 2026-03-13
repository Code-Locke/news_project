import feedparser
from flask import Flask

app = Flask(__name__)

@app.route('/')

def index():
    return "Index:"

@app.route('/home')

def home():
    return "Home:"

if __name__ == '__main__':
    app.run(debug=True)