from flask import Flask, jsonify
from flask_cors import CORS
from nytclassg4limit import ArticleProcessor

app = Flask(__name__)
CORS(app)  # This enables CORS

db_name = 'newsdb'
collection_name = 'articles'

processor = ArticleProcessor(None, db_name, collection_name, None)

@app.route('/articles', methods=['GET'])
def get_articles():
    articles = []
    for article in processor.collection.find():
        del article['_id']  # Remove the _id field as it's not JSON serializable
        articles.append(article)
    return jsonify(articles)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

