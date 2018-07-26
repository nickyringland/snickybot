from slackclient import SlackClient
from icalevents import icalevents
from datetime import datetime, timezone, timedelta
import time
import os

slack_token = os.environ["SLACK_API_TOKEN"]
sc = SlackClient(slack_token)
url = 'https://calendar.google.com/calendar/ical/ncss.edu.au_7hiaq9tlca1lsgfksjss4ejc4s%40group.calendar.google.com/private-23775cab8b8397efb35dd7f2b6e67d84/basic.ics'
channel="CBXDYDGFP"

def get_next_tutor(now):
  evs = icalevents.events(url=url)
  #'all_day', 'copy_to', 'description', 'end', 'start', 'summary', 'time_left', 'uid'
  evs.sort(key=lambda ev: now - ev.start, reverse=True)

  for ev in evs:
    if (now - ev.start).total_seconds() < 0:
      #this is the first one in the future
      return(ev)

def event_is_same(ev1, ev2):
  if not ev1 or not ev2:
    return ev1 == ev2
  return ev1.uid == ev2.uid


announced_next_tutor = None
while True:
  now = datetime.now(timezone.utc)
  next_tutor = get_next_tutor(now)
  impending_tutor_time = -(now - next_tutor.start)
  if not event_is_same(next_tutor, announced_next_tutor):
    sc.api_call(
      "chat.postMessage",
      channel=channel,
      text=":smile: Next tutor {} in {}".format(next_tutor.summary, (impending_tutor_time))
    )
    announced_next_tutor = next_tutor
  print('checking ical next tutor {}.'.format(announced_next_tutor.summary))
  if impending_tutor_time.total_seconds() < (5*60):
    sc.api_call(
      "chat.postMessage",
      channel=channel,
      text="remind {} starting soon! plz ack".format(next_tutor.summary)
    )

  time.sleep(60)
  #time.sleep(td.total_seconds())



