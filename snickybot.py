from slackclient import SlackClient
from icalevents import icalevents
from datetime import datetime, timezone, timedelta
import os

slack_token = os.environ["SLACK_API_TOKEN"]
sc = SlackClient(slack_token)
url = 'https://calendar.google.com/calendar/ical/ncss.edu.au_7hiaq9tlca1lsgfksjss4ejc4s%40group.calendar.google.com/private-23775cab8b8397efb35dd7f2b6e67d84/basic.ics'


def get_next_tutor(now):
  evs = icalevents.events(url=url)
  #'all_day', 'copy_to', 'description', 'end', 'start', 'summary', 'time_left', 'uid'
  evs.sort(key=lambda ev: now - ev.start, reverse=True)

  for ev in evs:
    if (now - ev.start).total_seconds() < 0:
      #this is the first one in the future
      return(ev)

now = datetime.now(timezone.utc)
next_tutor = get_next_tutor(now)
sc.api_call(
  "chat.postMessage",
  channel="CBXDYDGFP",
  text=":smile: Next tutor {} in {}".format(next_tutor.summary, (-(now - next_tutor.start)))
)
