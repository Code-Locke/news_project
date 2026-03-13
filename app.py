import feedparser
from flask import Flask

app = Flask(__name__)

@app.route('/')

def home():
    return "Home: PlaceHolder for now"

@app.route('/about')

def about():
    return "This is an open-source News Aggregator developed by Yours Truly"

@app.route('/features')

def features():
    return "Features will go here maybe/ HTML Template Rendering will be added"

if __name__ == '__main__':
    app.run(debug=True)