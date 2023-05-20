import os
import requests
import argparse
from xml.etree import ElementTree
from goose3 import Goose
import openai
from pymongo import MongoClient
from datetime import datetime
from bs4 import BeautifulSoup
import requests
import re
import logging
from newspaper import Article

logging.basicConfig(level=logging.INFO)

# Use a configuration file for these parameters
RSS_URL_BBC = 'http://feeds.bbci.co.uk/news/rss.xml'
RSS_URL_SCMP = 'https://www.scmp.com/rss/91/feed'
RSS_URL_NYT = 'https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml'
RSS_URL_StateDept = 'https://www.state.gov/rss-feed/arms-control-and-international-security/feed/'
DB_NAME = 'newsdb'
COLLECTION_NAME = 'articles'

class NewsSource:
    def __init__(self, rss_url, openai_key, num_articles, insert_db, summarize, print_data):
        self.rss_url = rss_url
        self.openai_key = openai_key
        self.num_articles = num_articles
        self.insert_db = insert_db
        self.summarize = summarize
        self.print_data = print_data
        self.client = None
        self.db = None
        self.collection = None

        if self.insert_db:
            try:
                self.client = MongoClient('mongodb://localhost:27017/')
                self.db = self.client[DB_NAME]
                self.collection = self.db[COLLECTION_NAME]
            except Exception as e:
                logging.error(f"Failed to connect to MongoDB: {e}")
                return None

    def summarize_article(self, article):
        openai.api_key = self.openai_key
        truncated_article = article[:4000]
        prompt = f"Summarize news article in 10 words:\n\n{truncated_article}\n\nSummary:"
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a master summarizer. Answer as concisely as possible into 70 words. Remove politically biasing language"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=250,
            temperature=0.5,
        )
        summary = response['choices'][0]['message']['content'].strip()
        return summary

    def remove_duplicates_in_mongodb(self):
        pipeline = [
            {"$group": {
                "_id": "$Final URL",
                "count": {"$sum": 1},
                "ids": {"$addToSet": "$_id"},
                "latest": {"$max": "$Publish Date"}
            }},
            {"$match": {"count": {"$gt": 1}}}
        ]
        duplicates = list(self.collection.aggregate(pipeline))
        for duplicate in duplicates:
            ids_to_remove = [doc_id for doc_id in duplicate['ids'] if self.collection.find_one({"_id": doc_id})['Publish Date'] != duplicate['latest']]
            self.collection.delete_many({"_id": {"$in": ids_to_remove}})

    def print_article_data(self, data):
        if self.print_data:
            logging.info("\nArticle data:")
            for key, value in data.items():
                logging.info(f"{key}: {value}")
            logging.info("-" * 50)

    def process_articles(self):
        raise NotImplementedError

class NewYorkTimes(NewsSource):
    def __init__(self, rss_url, openai_key, num_articles, insert_db, summarize, print_data):
        super().__init__(rss_url, openai_key, num_articles, insert_db, summarize, print_data)

    def process_articles(self):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }

        response = requests.get(self.rss_url)
        soup = BeautifulSoup(response.text, 'xml')
        articles = soup.findAll('item')[:self.num_articles]

        for article in articles:
            url = article.find('link').text
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.content, 'html.parser')

            content_main = soup.find(['main', 'article'])

            if content_main is None:
                logging.error(f"Couldn't find the main content of the article at {url}.")
                continue

            for element in content_main(['style', 'script', 'EndOfContentLinksGrid']):
                element.extract()

            paragraphs = content_main.find_all('p')
            article_text = ' '.join(paragraph.text for paragraph in paragraphs)

            article_title = soup.title.string if soup.title else "N/A"

            # Extract the author's name
            author_element = soup.find('span', class_='byline-prefix').find_next_sibling('span').find('a')
            if author_element is None:
                logging.error(f"Couldn't find the author of the article at {url}.")
                article_authors = ["Unknown"]
            else:
                article_authors = [author_element.get_text()]

            article_date = datetime.strptime(article.pubDate.text, '%a, %d %b %Y %H:%M:%S %z')

            # Modify the article text to remove advertisement phrases
            start_phrase = "Advertisement Supported by"
            end_phrase = " Advertisement"
            if article_text.startswith(start_phrase):
                article_text = article_text[len(start_phrase):].strip()
            if article_text.endswith(end_phrase):
                article_text = article_text[:-len(end_phrase)].strip()

            data = {
                'Final URL': url,
                'Article Title': article_title,
                'Article Text': article_text,
                'Article Authors': article_authors,
                'Publish Date': article_date,
                'Article Summary': self.summarize_article(article_text) if self.summarize else None,
            }
            self.print_article_data(data)
            if self.insert_db:
                self.collection.insert_one(data)
        if self.insert_db:
            self.remove_duplicates_in_mongodb()

class BBCNewsSource(NewsSource):
    def __init__(self, rss_url, openai_key, num_articles, insert_db, summarize, print_data):
        super().__init__(rss_url, openai_key, num_articles, insert_db, summarize, print_data)

    def process_articles(self):
        response = requests.get(self.rss_url)
        soup = BeautifulSoup(response.text, 'xml')
        articles = soup.findAll('item')[:self.num_articles]

        for article in articles:
            url = article.find('link').text

            # Use requests and Beautiful Soup to extract information
            article_response = requests.get(url)
            article_soup = BeautifulSoup(article_response.text, 'html.parser')

            # Try to find the main or article tag containing the text
            content_main = article_soup.find(['main', 'article'])

            # If we can't find the content, print an error message and stop the script
            if content_main is None:
                logging.error(f"Couldn't find the main content of the article at {url}.")
                continue

            # Remove elements that we do not want
            for element in content_main(['style', 'script']):
                element.extract()  # discard unwanted parts

            # Find all paragraphs within the main or article tag
            paragraphs = content_main.find_all('p')

            # Concatenate the text of each paragraph to form the article text
            article_text = ' '.join(paragraph.text for paragraph in paragraphs)

            # You can add additional logic here to extract the title, authors, and date
            article_title = article_soup.title.string if article_soup.title else "N/A"
            article_authors = ["BBC"]  # BBC articles typically do not list authors
            article_date = datetime.now()

            data = {
                'Final URL': url,
                'Article Title': article_title,
                'Article Text': article_text,
                'Article Authors': article_authors,
                'Publish Date': article_date,
                'Article Summary': self.summarize_article(article_text) if self.summarize else None,
            }
            self.print_article_data(data)
            if self.insert_db:
                self.collection.insert_one(data)
        if self.insert_db:
            self.remove_duplicates_in_mongodb()

class StateDeptNewsSource(NewsSource):
    def __init__(self, rss_url, openai_key, num_articles, insert_db, summarize, print_data):
        super().__init__(rss_url, openai_key, num_articles, insert_db, summarize, print_data)


    def process_articles(self):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.1234.567 Safari/537.36'
        }
        response = requests.get(self.rss_url, headers=headers)
        root = ElementTree.fromstring(response.content)
        articles = root.findall('./channel/item')[:self.num_articles]




        for article in articles:
            soup = BeautifulSoup(article.find('description').text, 'html.parser')
            data = {
                'Final URL': article.find('link').text,
                'Article Title': article.find('title').text,
                'Article Text': article.find('description').text,
                'Article Text': soup.get_text(),  # get text without HTML tags
                'Article Authors': [article.find('{http://purl.org/dc/elements/1.1/}creator').text],
                'Publish Date': datetime.strptime(article.find('pubDate').text, '%a, %d %b %Y %H:%M:%S %z'),
                'Article Summary': self.summarize_article(article.find('description').text) if self.summarize else None,
            }
            self.print_article_data(data)
            if self.insert_db:
                self.collection.insert_one(data)
        if self.insert_db:
            self.remove_duplicates_in_mongodb()



class SCMPNewsSource(NewsSource):
    def __init__(self, rss_url, openai_key, num_articles, insert_db, summarize, print_data):
        super().__init__(rss_url, openai_key, num_articles, insert_db, summarize, print_data)

    def extract_article_urls(self):
        try:
            response = requests.get(self.rss_url)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to fetch RSS feed: {e}")
            return []

        root = ElementTree.fromstring(response.content)
        article_urls = [item.find('link').text for item in root.findall('./channel/item')][:self.num_articles]
        return article_urls

    def process_articles(self):
        article_urls = self.extract_article_urls()
        g = Goose()
        for url in article_urls:
            article = g.extract(url=url)
            data = {
                'Final URL': url,
                'Article Title': article.title,
                'Article Text': article.cleaned_text,
                'Article Authors': article.authors,
                'Publish Date': article.publish_date if article.publish_date else datetime.now(),
                'Article Summary': self.summarize_article(article.cleaned_text) if self.summarize else None,
            }
            self.print_article_data(data)
            if self.insert_db:
                self.collection.insert_one(data)
        if self.insert_db:
            self.remove_duplicates_in_mongodb()




def main():
    parser = argparse.ArgumentParser(description='Fetch and process news articles.')
    parser.add_argument('--source', choices=['SCMP', 'NYT', 'StateDept', 'BBC'], required=True)
    parser.add_argument('--num_articles', type=int, default=5)
    parser.add_argument('--insert_db', action='store_true')
    parser.add_argument('--summarize', action='store_true')
    parser.add_argument('--print', dest='print_data', action='store_true')
    args = parser.parse_args()


    if args.source == 'SCMP':
        news_source = SCMPNewsSource(RSS_URL_SCMP, os.getenv('OPENAI_KEY'), args.num_articles, args.insert_db, args.summarize, args.print_data)
    elif args.source == 'NYT':
        news_source = NewYorkTimes(RSS_URL_NYT, os.getenv('OPENAI_KEY'), args.num_articles, args.insert_db, args.summarize, args.print_data)
    elif args.source == 'StateDept':
        news_source = StateDeptNewsSource(RSS_URL_StateDept, os.getenv('OPENAI_KEY'), args.num_articles, args.insert_db, args.summarize, args.print_data)
    elif args.source == 'BBC':
        news_source = BBCNewsSource(RSS_URL_BBC, os.getenv('OPENAI_KEY'), args.num_articles, args.insert_db, args.summarize, args.print_data)
    news_source.process_articles()

if __name__ == "__main__":
    main()
