import re
import os.path
from links_and_paths import webhook_url, published_transfers_path, rink_live_spreadsheet_id, rink_live_tab_name, gopher_puck_live_shreadsheet_id, gopher_puck_live_tab_name, token_json_path, credentials_json_path
from discord_webhook import DiscordWebhook
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

inter_university_transfers = []
mtu_strings = ['Michigan Technological University', 'Michigan Tech', 'MTU']

# Access and load the data in a certain tab of the specified Google Sheets spreasheet.
def get_portal_spreadsheet_data(spreadsheet_id, sheet_name):
    scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
    creds = None

    # token.json stores the user's access and refresh tokens.
    # It's created automatically when the authorization flow completes for the first time.
    if os.path.exists(token_json_path + 'token.json'):
        creds = Credentials.from_authorized_user_file(token_json_path + 'token.json', scopes)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_json_path + 'credentials.json', scopes
            )
            creds = flow.run_local_server(port=0)

        # Save the credentials for the next run
        with open(token_json_path + 'token.json', 'w') as token:
            token.write(creds.to_json())
    try:
        service = build('sheets', 'v4', credentials=creds)

        # Call the Sheets API
        sheet = service.spreadsheets()
        result = (
            sheet.values()
            .get(spreadsheetId=spreadsheet_id, range=sheet_name)
            .execute()
        )
        values = result.get('values', [])

        if not values:
            print('No data found.')
            return []

        return values
    except HttpError as err:
        print(err)
        return []

# Parse the provided data corresponding to a certain transfer portal spreadsheet. Look for mentions of players transferring to or from Michigan Tech and assemble a list of them.
def process_portal_spreadsheet(portal_spreadsheet_data, starting_row, origin_team_column, position_column, player_name_column, destination_team_column, date_added_column):
    # Loop through each row in the spreadsheet data.
    for row in portal_spreadsheet_data[starting_row:]:
        # Handle situations where sometimes a row's columns are empty and represented as not part of the row instead of just an empty string.
        try:
            origin_team = row[origin_team_column].strip()

            if origin_team == '':
                raise IndexError('The origin team is not listed!')
        except IndexError:
            # If there's no origin team listed, move on to the next row.
            continue

        try:
            date_column_string = row[date_added_column].strip()

            if date_column_string == '':
                raise IndexError('The date added is not listed!')
        except IndexError:
            # If there's no date listed for when the player entered the portal, move on to the next row.
            continue

        try:
            destination_team = '?' if row[destination_team_column] == '' else row[destination_team_column].strip()     
        except IndexError:
            destination_team = '?'

        try:
            # A player's position will be represented as either F, D, or G.
            position = 'player' if row[position_column][0].upper() == '' else row[position_column].strip()[0].upper()
        except IndexError:
            position = 'player'

        # If Michigan Tech is a player's origin or destination team, record information about the transfer: player name, position, origin team, destination team.
        if origin_team in mtu_strings or destination_team in mtu_strings:
            # Parse out and re-assemble the date added in order to account for differences in different sheets' date format, typos, etc.
            date_parts = re.search(r'(\d+)\/(\d+)\/(\d+)', date_column_string)
            month = date_parts.group(1)
            day = date_parts.group(2)
            year = date_parts.group(3)

            if not year.startswith('20'):
                year = '20' + year

            date_added = month + '/' + day + '/' + year

            # If either the origin or destination team is Michigan Tech, use a common name to avoid saying 'Michigan Technological University' or 'MTU'.
            if origin_team in mtu_strings:
                origin_team = 'Michigan Tech'

            if destination_team in mtu_strings:
                destination_team = 'Michigan Tech'

            current_transfer = [date_added, row[player_name_column].strip(), position, origin_team, destination_team]
            already_present = False
            
            # Look for the player's name in our list transfers we've already compiled from other transfer portal spreadsheets.
            for existing_transfer in inter_university_transfers:
                if current_transfer[1] == existing_transfer[1]:
                    # If we already saw this transfer in another transfer portal spreadsheet, check to see if it had a destination team listed.
                    already_present = True

                    if current_transfer[2] != 'player' and existing_transfer[2] == 'player':
                        # If the previous mention of this transfer didn't list the player's position, but this spreadsheet does, add it.
                        existing_transfer[2] == current_transfer[2]

                    if current_transfer[4] != '?' and existing_transfer[4] == '?':
                        # If the previous mention of this transfer didn't list a destination team, but this spreadsheet does, add it.
                        existing_transfer[4] = current_transfer[4]
                        break

            # If this tranfer was not previously recorded, add it to our list of transfers to publish (as long as we didn't publish it in a previous invocation).
            if not already_present:
                inter_university_transfers.append(current_transfer)

# Examine each transfer involving Michigan Tech that was gathered from the transfer portal spreadsheets.
# Send out a notification for any that haven't been published yet or completed (published without a destination team).
def send_transfers_to_discord():
    # Gather a list of lines from the file keeping track of which transfers have already been published.
    with open(published_transfers_path + 'published_transfers.txt', 'r') as published_transfers_file:
        published_transfers_file_lines = published_transfers_file.readlines()

    with open(published_transfers_path + 'published_transfers.txt', 'w') as published_transfers_file:
        # For each transfer that identified in the portal spreadsheets, check if it exists in published_transfers.txt (it was already published).
        for transfer in inter_university_transfers:
            date_added = transfer[0]
            player_name = transfer[1]
            player_position = transfer[2]
            origin_team = transfer[3]
            destination_team = transfer[4]

            transfer_already_published = False

            for published_transfer in published_transfers_file_lines:
                # Separate each line from published_transfers.txt into an array of its parts.
                published_transfer_parts = re.split(',', published_transfer.rstrip())

                # If we find a matching transfer that was already published (having the same transfer portal entry date and player name), check if the
                # previous publish was incomplete (didn't list a destination team). If it was, send it again to announce the destination team.
                if date_added == published_transfer_parts[0] and player_name == published_transfer_parts[1]:
                    transfer_already_published = True
                    
                    # If the version of the transfer from published_transfers.txt listed '?' as the destination team, and the version that was identified
                    # in the latest invocation's destination team is NOT unknown, send out a second, complete notification.
                    if published_transfer_parts[4] == '?' and destination_team != '?':
                        message = '__***MTU Hockey Transfer Alert***__\n%s %s %s has transferred to %s.' % (origin_team, player_position, player_name, destination_team)
                        webhook = DiscordWebhook(url=webhook_url, content=message)
                        webhook.execute()

                        # When recording this transfer in published_transfers.txt, we want it to be the version that is complete (lists a destination team).
                        published_transfers_file.write('%s,%s,%s,%s,%s\n' % (date_added, player_name, player_position, origin_team, destination_team))
                        break

                    published_transfers_file.write(published_transfer)

            if not transfer_already_published:
                # A new transfer has been identified, so publish a notification for it.
                if destination_team == '?':
                    message = '__***MTU Hockey Transfer Alert***__\n%s %s %s has entered the transfer portal.' % (origin_team, player_position, player_name)
                else:
                    message = '__***MTU Hockey Transfer Alert***__\n%s %s %s has transferred to %s.' % (origin_team, player_position, player_name, destination_team)
                
                webhook = DiscordWebhook(url=webhook_url, content=message)
                webhook.execute()
                published_transfers_file.write('%s,%s,%s,%s,%s\n' % (date_added, player_name, player_position, origin_team, destination_team))

def main():
    rink_live_portal_data = get_portal_spreadsheet_data(rink_live_spreadsheet_id, rink_live_tab_name)
    gopher_puck_live_portal_data = get_portal_spreadsheet_data(gopher_puck_live_shreadsheet_id, gopher_puck_live_tab_name)
    process_portal_spreadsheet(rink_live_portal_data, 2, 1, 6, 0, 11, 15)
    process_portal_spreadsheet(gopher_puck_live_portal_data, 1, 2, 3, 1, 5, 0)
    send_transfers_to_discord()

if __name__ == '__main__': 
    main()