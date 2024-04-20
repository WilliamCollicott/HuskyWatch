import datetime
import re
import feedparser
import requests
from links_and_paths import webhook_url, transaction_ids_path
from discord_webhook import DiscordWebhook
from bs4 import BeautifulSoup

# This list is used to check if transaction Michigan Tech is involved in is a player transferring to/from another university.
ncaa_d1_team_ids = ['2453', '1252', '18066', '1273', '35387', '790',  '2319', '911',  '633',   '1214',  '1320',  '1583', '685',
                    '913',  '1859', '706',   '840',  '1917',  '728',  '1339', '1792', '35273', '30556', '1866',  '1871', '1248',
                    '1157', '548',  '1520',  '2110', '1465',  '925',  '1549', '2118', '1551',  '713',   '2078',  '2039', '1543',
                    '1758', '2299', '773',   '1772', '4991',  '1038', '1366', '1915', '2071',  '1362',  '2034',  '606',  '1074',
                    '803',  '776',  '1794',  '708',  '1136',  '1137', '1554', '2745', '710',   '452',   '1250',  '786']

# This method parses a transaction's description section and assembles the string representing the message to be published.
def construct_message(title, decoded_description, type):
    # Parse out the sections of the description we're interested in.
    details = re.search(r'(Status: .*)<br/>\n(Date: .*)<br/>\nPlayer: <a href=\"(.*)\">', decoded_description)
    status = details.group(1)
    date = details.group(2)
    ep_player_page = details.group(3)

    # Assemble the formatted string.
    message = '__***MTU Hockey %s Alert***__\n%s\n%s\n%s' % (type, title, status, date)

    # If the transaction's description has 'additional information' (not all will have this), add it onto the message.
    if re.search(r'Information:', decoded_description):
        information = re.search(r'(Information: .*)<br/>', decoded_description).group(1)
        message += ('\n' + information)

    message += ('\n[EliteProspects Player Page](<%s>)' % (ep_player_page))

    # Attach the player page's profile photo to the message if it exists.
    ep_player_page_data = requests.get(ep_player_page)
    ep_player_page_html = BeautifulSoup(ep_player_page_data.text, 'html.parser')
    ep_player_page_picture_section = ep_player_page_html.find('div', {'class': 'ep-entity-header__main-image'})
    ep_player_page_picture_search = re.search(r'url\([\"\'](.*)[\"\']\);', ep_player_page_picture_section['style'])

    if ep_player_page_picture_search and (ep_player_page_picture_search.group(1) != 'https://static.eliteprospects.com/images/player-fallback.jpg'):
        # The player's page has a profile photo.
        print(ep_player_page_picture_search.group(1))
        return message, ep_player_page_picture_search.group(1)
    else:
        # The player's page does not have a profile photo.
        return message, None

# For a given transaction, delegate the message construction to construct_message() and publish it.
def send_transaction_to_discord(transaction_id, title, decoded_description, type):
    # Assamble the message to be published.
    message, player_picture_path = construct_message(title, decoded_description, type)

    # Attach the player's image if it exists.
    if player_picture_path is not None:
        webhook = DiscordWebhook(url=webhook_url, content=message, embeds=[{ 'image': { 'url': 'https:' + player_picture_path } }])
    else:
        webhook = DiscordWebhook(url=webhook_url, content=message)

    # Publish the message and optional image to Discord.
    webhook.execute()

    # Record the transaction's ID so we know not to publish it again it we still see it later on.
    with open(transaction_ids_path + 'transaction_ids.txt', 'a') as transaction_ids_file:
        date_and_time = datetime.datetime.now()
        transaction_ids_file.write(transaction_id + ',' + str(date_and_time) + '\n')

# Assemble a list of EliteProspects player page URLs representing future and former Michigan Tech players.
# This information will come from Michigan Tech's 'Where are they now' page.
def get_player_page_links():
    page_data = requests.get('https://www.eliteprospects.com/team/548/michigan-tech/where-are-they-now?sort=tp')
    page_html = BeautifulSoup(page_data.text, 'html.parser')
    page_player_tables = page_html.select('div.expandable-table-wrapper')

    # Create a list of player page URLs for all future and former players on Michigan Tech's 'Where Are They Now?' page.
    player_page_urls = re.findall(r'<a href=\"(https://www\.eliteprospects\.com/player/\d*/.*)\">.*</a>', str(page_player_tables))
    print(player_page_urls)
    return player_page_urls 

# Assemble list of transaction IDs representing transactions published less than 14 days ago.
# Remove lines from transaction_ids.txt representing transactions that are at least 14 days old.
def update_transaction_ids_file():
    # List of transaction IDs that we've published less than 14 days ago.
    transaction_ids_list = []
    script_invocation_time = datetime.datetime.now()

    # Loop through each transaction listed in transaction_ids.txt to determine if we still need to keep track of it.
    with open(transaction_ids_path + 'transaction_ids.txt', 'r') as transaction_ids_file:
        transaction_ids_file_lines = transaction_ids_file.readlines()

    # Clear the transaction_ids.txt file and only write back the lines whose transactions we still want to keep track of.
    with open(transaction_ids_path + 'transaction_ids.txt', 'w') as transaction_ids_file:
        for line in transaction_ids_file_lines:
            # For each line in the file, parse out it's transaction ID and date it was put into the file.
            line_parts = re.search(r'(\d*),(.*)', line)
            transaction_id = line_parts.group(1)
            transaction_datetime = datetime.datetime.strptime(line_parts.group(2), '%Y-%m-%d %H:%M:%S.%f')

            # If the transaction is older than 14 days, don't bother keeping track of it anymore.
            time_difference = script_invocation_time - transaction_datetime
            if time_difference.days >= 14:
                continue

            # If we published the transaction less than 14 days ago, continue to keep track of it.
            transaction_ids_list.append(transaction_id)
            transaction_ids_file.write(line)

    return transaction_ids_list

# This method examines each of the 50 most recent entries in the EliteProspects RSS transaction for mentions of Michigan Tech.
def process_feed(player_page_urls, transaction_ids_list):
    # Query the EliteProspects transfers RSS feed.
    feed = feedparser.parse('https://www.eliteprospects.com/rss/transfers')

    if len(feed) == 0:
        raise Exception('The list of RSS feed entries is 0')

    # In each the RSS feed's 50 most recent transactions, look for mentions of future, current, or former Michigan Tech players.
    for item in feed.entries:
        transaction_id = re.search(r'/t/(\d*)', item.guid).group(1)

        if transaction_id in transaction_ids_list:
            # If the transaction ID's transaction has already been published, move on to the next entry in the feed.
            continue

        decoded_description = str(BeautifulSoup(item.description, features='html.parser'))
        match_type = ''

        if re.search(r'From: <a href="https:\/\/www\.eliteprospects\.com\/team\/548\/', decoded_description):
            match = re.search(r'To: <a href="https:\/\/www\.eliteprospects\.com\/team\/(\d*)\/', decoded_description)
            if match:
                destination_team_id = match.group(1)
                if destination_team_id in ncaa_d1_team_ids:
                    # Do not process inter-university transfers in the EliteProspects transaction feed.
                    # Instead, later on, look for these kinds of transactions in a transfer portal spreadsheet.
                    continue

            # A player is leaving Michigan Tech.
            match_type = 'Departure'

        elif re.search(r'To: <a href="https:\/\/www\.eliteprospects\.com\/team\/548\/', decoded_description):
            match = re.search(r'From: <a href="https:\/\/www\.eliteprospects\.com\/team\/(\d*)\/', decoded_description)
            if match:
                origin_team_id = match.group(1)
                if origin_team_id in ncaa_d1_team_ids:
                    continue

            # A player is joining Michigan Tech.
            match_type = 'Arrival'
        else:
            # If Michigan Tech is not mentioned in the transaction, check to see if a future or former player is involved.
            for url in player_page_urls:
                if re.search(url, decoded_description):
                    # A future or former Michigan Tech player is involved in this transaction.
                    player_page_data = requests.get(url + '?league=NCAA')
                    player_page_html = BeautifulSoup(player_page_data.text, 'html.parser')
                    stats_section = player_page_html.find('div', {'id': 'league-stats'})
                    ncaa_section = stats_section.find_all('tr', {'data-league': 'NCAA'})

                    if len(ncaa_section) == 1 and ncaa_section[0].find('td', {'class': 'regular gp'}).text == '-*':
                        # The player is a future Michigan Tech player.
                        match_type = 'Future Player'
                    else:
                        # The player is a former Michigan Tech player.
                        match_type = 'Former Player'

        if match_type != '':
            send_transaction_to_discord(transaction_id, item.title, decoded_description, match_type)

def main():
    transaction_ids_list = update_transaction_ids_file()
    player_page_urls = get_player_page_links()
    process_feed(player_page_urls, transaction_ids_list)

if __name__ == '__main__': 
    main()