# HuskyWatch collects, organizes, and sends notifications about transfers related to Michgian Tech hockey
# 
# Transfer feeds are monitored for the following situations:
#   - Rumors, confirmations, etc. of junior hockey players committing to Michigan Tech
#   - Players who are committed to Michgian Tech change their junior team
#   - Current Michigan Tech players leaving for a different team
#   - Former Michigan Tech players changing the team they are playing on
#
# Author : William Collicott (MTU BS Computer Science, 2020)
# Email  : william@collicott.com

import datetime
import re
import feedparser
import requests
from links_and_paths import webhook_url, transaction_ids_path
from discord_webhook import DiscordWebhook
from bs4 import BeautifulSoup

# This method parses a transfer's description section and assembles the string representing the message to be published 
def construct_message(title, decoded_description, type):
    # Parse out the sections of the description we're interested in
    details = re.search(r'(Status: .*)<br/>\n(Date: .*)<br/>\nPlayer: <a href=\"(.*)\">', decoded_description)
    status = details.group(1)
    date = details.group(2)
    ep_player_page = details.group(3)

    # Assemble the formatted string
    message = '__***MTU Hockey %s Alert***__\n%s\n%s\n%s' % (type, title, status, date)

    # If the transfer's description has 'additional information' (not all will have this), add it onto the message
    if re.search(r'Information:', decoded_description):
        information = re.search(r'(Information: .*)<br/>', decoded_description).group(1)
        message += ('\n' + information)

    message += ('\n[EliteProspects Player Page](<%s>)' % (ep_player_page))

    # Attach the player page's profile photo to the message if it exists
    ep_player_page_data = requests.get(ep_player_page)
    ep_player_page_html = BeautifulSoup(ep_player_page_data.text, 'html.parser')
    ep_player_page_picture_section = ep_player_page_html.find('div', {'class': 'ep-entity-header__main-image'})
    ep_player_page_picture_search = re.search(r'url\([\"\'](.*)[\"\']\);', ep_player_page_picture_section['style'])

    if ep_player_page_picture_search and (ep_player_page_picture_search.group(1) != 'https://static.eliteprospects.com/images/player-fallback.jpg'):
        # The player's page has a profile photo
        print(ep_player_page_picture_search.group(1))
        return message, ep_player_page_picture_search.group(1)
    else:
        # The player's page does not have a profile photo
        return message, None

# For a given transaction, delegate the message construction to construct_message() and publish it
def send_discord_message(transaction_id, transaction_ids_list, title, decoded_description, type):
    if transaction_id in transaction_ids_list:
        # Don't send out an alert for this transfer if we've already sent it out
        return

    # Assamble the message to be published
    message, player_picture_path = construct_message(title, decoded_description, type)

    # Attach the player's image if it exists
    if player_picture_path is not None:
        webhook = DiscordWebhook(url=webhook_url, content=message, embeds=[{ 'image': { 'url': 'https:' + player_picture_path } }])
    else:
        webhook = DiscordWebhook(url=webhook_url, content=message)

    # Publish the message and optional image to Discord
    webhook.execute()

    # Record the transaction's ID so we know not to publish it again it we still see it later on
    with open(transaction_ids_path + 'transaction_ids.txt', 'a') as transaction_ids_file:
        date_and_time = datetime.datetime.now()
        transaction_ids_file.write(transaction_id + ',' + str(date_and_time) + '\n')

# Assemble the lists of players to look out for and the list of transactions that have already been published
def setup():
    transaction_ids_list = []           # list of transaction IDs that we've published less than 14 days ago
    transaction_lines_to_add_back = []  # transactions in transaction_ids.txt that were published less than 14 days ago
    script_invocation_time = datetime.datetime.now()

    # Loop through each transaction listed in transaction_ids.txt to determine if we still need to keep track of it
    with open(transaction_ids_path + 'transaction_ids.txt', 'r') as transaction_ids_file:
        transaction_ids_file_lines = transaction_ids_file.readlines()
        
        for line in transaction_ids_file_lines:
            # For each line in the file, parse out it's transaction ID and date it was put into the file
            line_parts = re.search(r'(\d*),(.*)', line)
            transaction_id = line_parts.group(1)
            transaction_datetime = datetime.datetime.strptime(line_parts.group(2), '%Y-%m-%d %H:%M:%S.%f')
            
            # If the transaction is older than 14 days, don't bother keeping track of it anymore
            time_difference = script_invocation_time - transaction_datetime
            if time_difference.days >= 14:
                continue

            # If we published the transaction less than 14 days ago, continue to keep track of it
            transaction_ids_list.append(transaction_id)
            transaction_lines_to_add_back.append(line)
            
    # Clear the transaction_ids.txt file and only write back the lines whose transactions we still want to keep track of
    with open(transaction_ids_path + 'transaction_ids.txt', 'w') as transaction_ids_file:
        for line in transaction_lines_to_add_back:
            transaction_ids_file.write(line)

    # Gather IDs for players of interest (will play for Michigan Tech in the future, or played for Michigan Tech before)
    poi_data = requests.get('https://www.eliteprospects.com/team/548/michigan-tech/where-are-they-now?sort=tp')
    poi_html = BeautifulSoup(poi_data.text, 'html.parser')
    poi_player_tables = poi_html.select('div.expandable-table-wrapper')

    # Create a list of player page URLs for all future and former players on Michigan Tech's "Where Are They Now?" page
    poi_player_page_urls = re.findall(r'<a href=\"(https://www\.eliteprospects\.com/player/\d*/.*)\">.*</a>', str(poi_player_tables))

    print(poi_player_page_urls)

    return poi_player_page_urls, transaction_ids_list

def process_feed(feed, poi_player_page_urls, transaction_ids_list):
    if len(feed) == 0:
        raise Exception("The list of RSS feed entries is 0")

    # In each the RSS feed's 50 most recent transfers, look for mentions of Michigan Tech or future or former players
    for item in feed.entries:
        transaction_id = re.search(r'/t/(\d*)', item.guid).group(1)
        decoded_description = str(BeautifulSoup(item.description, features='html.parser'))
        match_type = ''

        if re.search(r'From: <a href="https:\/\/www\.eliteprospects\.com\/team\/548\/', decoded_description):
            # A player is leaving Michigan Tech
            match_type = 'Departure'
        elif re.search(r'To: <a href="https:\/\/www\.eliteprospects\.com\/team\/548\/', decoded_description):
            # A player is joining Michigan Tech
            match_type = 'Arrival'
        else:
            # If Michigan Tech is not mentioned in the transfer, check to see if a future or former player is involved
            for url in poi_player_page_urls:
                if re.search(url, decoded_description):
                    # A future or former Michigan Tech player is involved in this transaction
                    poi_page_data = requests.get(url + '?league=NCAA')
                    poi_page_html = BeautifulSoup(poi_page_data.text, 'html.parser')
                    stats_section = poi_page_html.find('div', {'id': 'league-stats'})
                    ncaa_section = stats_section.find_all('tr', {'data-league': 'NCAA'})

                    if len(ncaa_section) == 1 and ncaa_section[0].find('td', {'class': 'regular gp'}).text == '-*':
                        # The player is a future MTU player
                        match_type = 'Future Player'
                    else:
                        # The player is a former MTU player
                        match_type = 'Former Player'

        if match_type != '':
            send_discord_message(transaction_id, transaction_ids_list, item.title, decoded_description, match_type)

def main():
    poi_player_page_urls, transaction_ids_list = setup()
    feed = feedparser.parse('https://www.eliteprospects.com/rss/transfers')
    process_feed(feed, poi_player_page_urls, transaction_ids_list)

if __name__ == "__main__": 
    main()