import arlo as Arlo
import logging
from datetime import timedelta, date, datetime
import json
import io as io
import os as os
import threading
import glob
import time
from flask import Flask, abort, request, Response
import requests
import webbrowser
from credentials import *

# FLASK STUFF
app = Flask(__name__)
G_URL = "https://accounts.google.com/o/oauth2/v2/auth"
REDIRECT_URI = "http://localhost:42069/auth"
SCOPE = "https://www.googleapis.com/auth/photoslibrary"

logging.basicConfig(level=logging.INFO)  # set default logger


# handles downloading of arlo footage
class SmartCameraBackup(threading.Thread):
    def __init__(self, database):
        threading.Thread.__init__(self)
        self.database = database

        self.arlo = Arlo.Arlo(ARLO_USERNAME, ARLO_PASSWORD)
        logging.info("[SmartCameraBackup] Logged in as " + ARLO_USERNAME)

    def run(self):
        while True:
            video_folder = setup_video_folder()  # creates "video" folder

            try:
                arlo = self.arlo

                today = (date.today() - timedelta(days=0)).strftime("%Y%m%d")
                seven_days_ago = (date.today() - timedelta(days=7)).strftime("%Y%m%d")

                library = arlo.GetLibrary(seven_days_ago, today)
                # logging.info("Library obtained")

                for recording in library:
                    # Get video as a chunked stream; this function returns a generator.
                    stream = arlo.StreamRecording(recording['presignedContentUrl'])
                    video_file_name = datetime.fromtimestamp(
                        int(recording['name']) // 1000).strftime(
                        '%Y-%m-%d %H-%M-%S') + ' ' + recording['uniqueId'] + '.mp4'

                    # if the video is already downloaded, then ignore it
                    if video_file_name in self.database['downloaded']:
                        logging.debug(video_file_name + " already downloaded")
                        continue

                    # Download video
                    with open(video_folder + '/' + video_file_name, 'wb') as f:
                        for chunk in stream:
                            f.write(chunk)
                        f.close()

                    logging.info('[SmartCameraBackup] Downloaded video ' + video_file_name + ' from ' + recording['createdDate'] + '.')
                    # Add to database
                    self.database['downloaded'].append(video_file_name)
                    # Save to file
                    save_database(self.database)

                # logging.info("Done downloading all footage. Napping for 1 min")
                time.sleep(1)

            except Exception as e:
                logging.error(e)


# Creates a video folder if it doesn't exist
def setup_video_folder():
    # create directory for video footage
    path = os.path.join(os.getcwd(), 'video')
    if not os.path.isdir(path):
        os.mkdir(path)
        logging.info("[setup_video_folder] Video path created")
    return path


# Handles uploading footage to google photos
class GooglePhotosBackup(threading.Thread):
    def __init__(self, database):
        threading.Thread.__init__(self)
        self.upload_dir = os.path.join(os.getcwd(), "video")
        self.database = database

    # Checks 'video' folder for new footage and uploads it to google photos
    def run(self):
        access_token = self.database['g_token']

        # TEST TOKEN WORKS
        # r = requests.get('https://photoslibrary.googleapis.com/v1/albums?access_token='+access_token)
        # resp = r.json()
        # print(resp)

        while True:

            # Get files in directory
            video_files = glob.glob(os.path.join(os.getcwd(), 'video', '*.mp4'))
            for video_file in video_files:
                # Grab the file name without the path
                filename = os.path.basename(video_file)

                # Check that video hasn't been uploaded
                if filename in self.database['uploaded']:
                    # Skip this file
                    logging.debug(filename + " already uploaded")
                    continue

                # Upload File
                headers = {'Content-type': 'application/octet-stream',
                           'X-Goog-Upload-File-Name': filename,
                           'X-Goog-Upload-Protocol': 'raw',
                           'Authorization': 'Bearer '+access_token
                           }
                data = open(video_file, 'rb').read()
                upload_video_url = "https://photoslibrary.googleapis.com/v1/uploads"
                r = requests.post(
                    url=upload_video_url,
                    data=data,
                    headers=headers,
                )
                logging.debug(r.content)
                upload_token = r.content.decode('UTF-8')  # Used to create a media item

                # create media item
                create_media_url = "https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate"
                headers = {
                    'Content-type': 'application/json',
                    'Authorization': 'Bearer '+access_token,
                }
                data = {
                    "newMediaItems": [
                        {
                            "description": "Uploaded using SmartCameraBackup by @mog3n on GitHub :)",
                            "simpleMediaItem": {
                                "uploadToken": upload_token
                            }
                        }
                    ]
                }
                m = requests.post(create_media_url,
                                  data=json.dumps(data),
                                  headers=headers,
                                  )
                # Check for create media error
                create_media_response = m.json()
                if "error" in create_media_response.keys():
                    logging.error("Error while trying to create media")
                    logging.info(create_media_response)
                    return
                logging.debug(m.json())
                logging.info("[GooglePhotosBackup] "+filename + " uploaded")
                # Add to database
                self.database['uploaded'].append(filename)
                # Save database
                save_database(self.database)

            # Finished uploading. Wait 1 minute
            # logging.info("Done uploading to Google Photos. Napping for 30 seconds. Power nap!")
            time.sleep(2)


class Status(threading.Thread):
    def __init__(self, db):
        threading.Thread.__init__(self)
        self.database =db

    def run(self):
        while True:
            logging.info("[Status] Uploaded: " + str(len(self.database['uploaded'])) +
                         " | Downloaded: " + str(len(self.database['downloaded'])))
            time.sleep(10)

# Saves the database file
def save_database(db):
    db_file_path = os.path.join(os.getcwd(), "data.json")
    file = io.open(db_file_path, 'w', encoding='utf-8')
    file.write(json.dumps(db))
    file.close()
    logging.debug("Database saved.")


# Gets the "database" file that keeps a record of all downloaded and uploaded video files
def get_database():
    database = {'uploaded': [], 'downloaded': [], 'g_token': '', 'code': ''}
    # Load database file
    db_file_path = os.path.join(os.getcwd(), "data.json")
    if os.path.exists(db_file_path):
        file = io.open(db_file_path, 'r', encoding='utf-8')
        database = json.loads(file.read())
        logging.info("[get_database] Database file loaded")
        file.close()
    else:
        # Writes the database dict to file
        file = io.open(db_file_path, 'w', encoding='utf-8')
        file.write(json.dumps(database))
        logging.info("[get_database] No file loaded: Creating one in memory")
        file.close()
    return database


# Flask callback endpoint
@app.route('/auth')
def auth():
    error = request.args.get('error', '')
    if error:
        logging.error(error)

    code = request.args.get('code', '')
    if code == None:
        logging.error('No code obtained.')

    # Make request for access token
    r = requests.post('https://oauth2.googleapis.com/token',
                      {
                          'code': code,
                          'client_id': CLIENT_ID,
                          'client_secret': CLIENT_SECRET,
                          'redirect_uri': REDIRECT_URI,
                          'grant_type': 'authorization_code'
                      })

    token = r.json()['access_token']
    refresh_token = r.json()['refresh_token']
    logging.debug('Access Token:'+token)
    save_access_token(token, refresh_token)
    # If all is good, then start backup.
    start_backup()
    return Response("{'message': 'Successfully logged into Google Photos'}", status=200, mimetype="application/json")


# Save access token to the data file
def save_access_token(token, refresh_token):
    # Get database, append token
    d = get_database()
    d['g_token'] = token
    d['refresh_token'] = refresh_token
    # Save to db
    db_file_path = os.path.join(os.getcwd(), "data.json")
    file = io.open(db_file_path, 'w')
    file.write(json.dumps(d))
    file.close()

class RefreshToken(threading.Thread):
    def __init__(self, db):
        threading.Thread.__init__(self)
        self.database = db

    def run(self):
        while True:
            logging.debug("Getting new access token using refresh token: " + self.database['refresh_token'])
            refresh_token = self.database['refresh_token']

            # use the refresh token to get a new access token
            r = requests.post('https://oauth2.googleapis.com/token',
                              {
                                  'client_id': CLIENT_ID,
                                  'client_secret': CLIENT_SECRET,
                                  'refresh_token': refresh_token,
                                  'grant_type': 'refresh_token'
                              })
            token_resp = r.json()
            access_token = token_resp['access_token']
            logging.info("[RefreshToken] New access token obtained")
            logging.debug(access_token)
            # Save access token
            save_access_token(access_token, refresh_token)

            logging.info("[RefreshToken] Sleeping for 10 mins")
            # Get a new access token every 10 minutes
            time.sleep(60*10)

# Starts backup to Google. This happens after token is obtained.
def start_backup():
    # Create video path
    setup_video_folder()

    # Get the database!
    db = get_database()

    # DEBUG: Remove file from db
    # db['uploaded'].remove("2020-01-18 13-59-57 MGAF5-100-42408585_5JX18875B0530.mp4")
    # db['downloaded'].remove("2020-01-18 13-59-57 MGAF5-100-42408585_5JX18875B0530.mp4")

    # Start getting new refresh tokens every 10 mins
    rt = RefreshToken(db)
    rt.start()

    # Start downloading Arlo footage
    smb = SmartCameraBackup(db)
    smb.start()

    # Start uploading to GCP
    gpb = GooglePhotosBackup(db)
    gpb.start()

    # Start status monitoring
    stats = Status(db)
    stats.start()


def main():
    # Open web browser to authenticate Google Photos API
    url = G_URL + "?client_id=" + CLIENT_ID + "&access_type=offline&redirect_uri="+REDIRECT_URI+"&scope="+SCOPE+"&response_type=code"

    # Check if a data json file already exists
    file_path = os.path.join(os.getcwd(), "data.json")
    if os.path.exists(file_path):
        # Start the backup process if it already exists
        start_backup()
    else:
        webbrowser.open(url, new=2)
        app.run(port=42069)


if __name__ == "__main__":
    main()
