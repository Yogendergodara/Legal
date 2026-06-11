"""Scrapy settings for the legal crawler."""

BOT_NAME = "legal_crawler"
SPIDER_MODULES = ["crawler"]
NEWSPIDER_MODULE = "crawler"
ROBOTSTXT_OBEY = True
DOWNLOAD_DELAY = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 2
USER_AGENT = "LegalAI-Crawler/1.0 (+https://yourco.in/crawler; contact@yourco.in)"
