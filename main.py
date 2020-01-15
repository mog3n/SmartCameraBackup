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
SCOPE = "https://www.googleapis.com/auth/photoslibrary.appendonly"

logging.basicConfig(level=logging.INFO)  # set default logger


# handles downloading of arlo footage
class SmartCameraBackup(threading.Thread):
    def __init__(self, database):
        threading.Thread.__init__(self)
        self.database = database

        self.arlo = Arlo.Arlo(ARLO_USERNAME, ARLO_PASSWORD)
        logging.info("Logged in as " + ARLO_USERNAME)

    def run(self):
        while True:
            video_folder = setup_video_folder()  # creates "video" folder

            try:
                arlo = self.arlo

                today = (date.today() - timedelta(days=0)).strftime("%Y%m%d")
                seven_days_ago = (date.today() - timedelta(days=7)).strftime("%Y%m%d")

                library = arlo.GetLibrary(seven_days_ago, today)
                logging.info("Library obtained")

                for recording in library:
                    # Get video as a chunked stream; this function returns a generator.
                    stream = arlo.StreamRecording(recording['presignedContentUrl'])
                    video_file_name = datetime.fromtimestamp(
                        int(recording['name']) // 1000).strftime(
                        '%Y-%m-%d %H-%M-%S') + ' ' + recording['uniqueId'] + '.mp4'

                    # if the video is already downloaded, then ignore it
                    if video_file_name in self.database['downloaded']:
                        logging.info(video_file_name + " already downloaded")
                        continue

                    # Download video
                    with open(video_folder + '/' + video_file_name, 'wb') as f:
                        for chunk in stream:
                            f.write(chunk)
                        f.close()

                    logging.info('Downloaded video ' + video_file_name + ' from ' + recording['createdDate'] + '.')
                    # Add to database
                    self.database['downloaded'].append(video_file_name)
                    self.save_database()

                logging.info("Done downloading all footage")
                time.sleep(60)

            except Exception as e:
                logging.error(e)

    # Save the database variable to a file
    def save_database(self):
        db_file_path = os.path.join(os.getcwd(), "data.json")
        file = io.open(db_file_path, 'w', encoding='utf-8')
        file.write(json.dumps(self.database))
        file.close()
        logging.debug("Database saved.")


# Creates a video folder if it doesn't exist
def setup_video_folder():
    # create directory for video footage
    path = os.path.join(os.getcwd(), 'video')
    if not os.path.isdir(path):
        os.mkdir(path)
        logging.info("Video path created")
    return path


# Handles uploading footage to google photos
class GooglePhotosBackup(threading.Thread):
    def __init__(self, database):
        threading.Thread.__init__(self)
        self.upload_dir = os.path.join(os.getcwd(), "video")
        self.database = database

    def run(self):
        # get all files in the directory
        while True:
            video_files = glob.glob(os.path.join(os.getcwd(), 'video', '*.mp4'))
            # Finished uploading. Wait 1 minute
            # logging.info("Done uploading to Google Photos")
            time.sleep(5)


# Gets the "database" file that keeps a record of all downloaded and uploaded video files
def get_database():
    database = {'uploaded': [], 'downloaded': [], 'g_token': ''}
    # Load database file
    db_file_path = os.path.join(os.getcwd(), "data.json")
    if os.path.exists(db_file_path):
        file = io.open(db_file_path, 'r', encoding='utf-8')
        database = json.loads(file.read())
        logging.info("Database file loaded")
        file.close()
    else:
        # Writes the database dict to file
        file = io.open(db_file_path, 'w', encoding='utf-8')
        file.write(json.dumps(database))
        logging.info("No file loaded: Creating one in memory")
        file.close()
    return database


@app.route('/auth')
def auth():
    error = request.args.get('error', '')
    if error:
        logging.error(error)

    code = request.args.get('code', '')

    r = requests.post('https://oauth2.googleapis.com/token',
                      {
                          'code': code,
                          'client_id': CLIENT_ID,
                          'client_secret': CLIENT_SECRET,
                          'redirect_uri': REDIRECT_URI,
                          'grant_type': 'authorization_code'
                      })

    token = r.json()['access_token']
    logging.info('Token:'+token)
    save_access_token(token)
    logging.info("G Token obtained: " + token)
    # If all is good, then start backup.
    start_backup()
    return Response("{'message': 'Successfully logged into Google Photos'}", status=200, mimetype="application/json")


def save_access_token(token):
    # Get database, append token
    d = get_database()
    d['g_token'] = token
    # Save to db
    db_file_path = os.path.join(os.getcwd(), "data.json")
    file = io.open(db_file_path, 'w')
    file.write(json.dumps(d))
    file.close()


# Starts backup to Google. This happens after token is obtained.
def start_backup():
    # Otherwise, start the app!
    db = get_database()
    # Start downloading Arlo footage
    smb = SmartCameraBackup(db)
    smb.start()
    # Start uploading to GCP
    gpb = GooglePhotosBackup(db)
    gpb.start()


def main():
    # Open web browser for Google Photos API
    url = G_URL + "?client_id=" + CLIENT_ID + "&redirect_uri="+REDIRECT_URI+"&scope="+SCOPE+"&response_type=code"
    webbrowser.open(url, new=2)
    app.run(port=42069)


if __name__ == "__main__":
    main()
