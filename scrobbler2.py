#!/usr/bin/env python
# -*- coding: utf-8 -*-

import urllib, urllib2
from time import mktime
from datetime import datetime, timedelta
from hashlib import md5
from httplib import BadStatusLine

__revision__ = "$Id: scrobbler2.py 38 2011-02-21 19:38:14Z chickenzilla $"

DEBUG = {"Now playing":False, "Flush":False, "Connection":False, "Submit":False}
from socket import setdefaulttimeout, getdefaulttimeout
setdefaulttimeout(20.0)

def debug(cat, text):
    if DEBUG.setdefault(cat, False):
        print "[Scrobbler2] %s : %s " % (cat, text)

class BackendError(Exception):
   "Raised if the AS backend does something funny"
   pass


class AuthError(Exception):
   "Raised on authencitation errors"
   pass


class PostError(Exception):
   "Raised if something goes wrong when posting data to AS"
   pass


class SessionError(Exception):
   "Raised when problems with the session exist"
   pass


class ProtocolError(Exception):
   "Raised on general Protocol errors"
   pass


class Scrobbler:
    def __init__(self, url="http://post.audioscrobbler.com/",
            handshake_delay=0, max_cache=5, protocol_version='1.2'):
        self.url = url
        self.session_id = None
        self.post_url = None
        self.now_url = None
        self.last_handshake = None
        self.hanshake_delay = handshake_delay
        self.submit_cache = []
        self.max_cache = max_cache
        self.protocol_version = protocol_version
        self.hard_fails = 0

    def __repr__(self):
        return self.url

    def connect(self, user, password, hashpw=False,
            client=('tst', '1.0'), hs='true'):
        """
        Connect to the url, with the user and the password.
        The password can be hashed with the haslib :
        hashlib.md5("pasword").hexdigest(), or a plain password and
        hashpw to 'True'.
        The client is a tuple : (client_id, client_version).
        Do not touch hs.
        For lastfm, more informations here : 
        http://www.audioscrobbler.net/development/protocol/
        """
        self.user = user
        self.client = client
        if self.last_handshake:
            next_allowed_hs = (self.last_handshake +
                    timedelta(seconds=self.hanshake_delay))
            if datetime.now() < next_allowed_hs:
                delta = next_allowed_hs - datetime.now()
                raise ProtocolError(("Please wait another %d seconds until next"
                    " handshake (login) attempt.") % delta.seconds)
        self.last_handshake = datetime.now()
        tstamp = int(mktime(datetime.now().timetuple()))
        if hashpw:
            password = md5(password).hexdigest()
        self.password = password
        token = md5("%s%d" % (password, int(tstamp))).hexdigest()
        values = {
            'hs': hs,
            'p' : self.protocol_version,
            'c': client[0],
            'v': client[1],
            'u': user,
            't': tstamp,
            'a': token
            }
        debug("Connection", "%s ..." % self)
        debug("Connection", values)
        data = urllib.urlencode(values)
        req = urllib2.Request("%s?%s" % (self.url, data))
        try:
            response = urllib2.urlopen(req)
        except BadStatusLine:
            print "Bad status line"
            return False
        lines = response.read().split('\n')
        debug("Connection", lines)

        if lines[0] == 'BADAUTH':
            raise AuthError('Bad username/password')
        elif lines[0] == 'BANNED':
            raise Exception("This client-version was banned by Audioscrobbler."
                    "Please contact the author of this module !")
        elif lines[0] == 'BADTIME':
            raise ValueError("Your system time is out of sync with"
                    " Audioscrobbler. Consider using an NTP-client to keep you"
                    " system time in sync.")
        elif lines[0].startswith('FAILED'):
            self.handle_hard_error()
            raise BackendError("Authencitate with server failed. Reason: %s" %
                    lines[0])
        elif lines[0] == "OK":
            self.session_id = lines[1]
            self.now_url = lines[2]
            self.post_url = lines[3]
            self.hard_fails = 0
            self.user_url = ""
            userinfos = {'libre.fm':'http://alpha.libre.fm/user/%n',
                    'audioscrobbler.com':'http://www.lastfm.fr/user/%n'}
            for key in userinfos:
                if key in self.post_url:
                    self.user_url = userinfos[key].replace('%n', self.user)
                    break
            return True
        else:
            self.handle_hard_error()
        return False

    def handle_hard_error(self):
        """ Handles hard errors. """
        if self.hanshake_delay == 0:
            self.hanshake_delay = 60
        elif self.hanshake_delay < 120 * 60:
            self.hanshake_delay *= 2
        if self.hanshake_delay > 120 * 60:
            self.hanshake_delay = 120 * 60
        self.hard_fails += 1
        if self.hard_fails == 3:
            self.session_id = None

    def now_playing(self, artist, track, album="", length="", trackno="",
            mbid="", inner_call=0):
        """
        Tells the server what is currently running in your player. This won't
        affect the user-profile on last.fm. To do submissions, use the "submit"
        method
        
        artist, track, album don't need explainations, length is the track
        length in seconds, trackno is the track number, and mbid the MusicBrainz
        Track ID.
        do not use inner_call
        Returns True on success ans False on failure.
        """

        if not self.session_id:
            raise AuthError("Please login first. (No session available)")
        if not self.post_url:
            raise PostError("Unable to post data. Post URL was empty!")
        if length != "" and not isinstance(length, int):
            raise TypeError("Lenght should be of type int")
        if trackno != "" and not isinstance(trackno, int):
            raise TypeError("Trackno should be of type int")

        values = { 's':self.session_id,
                   'a':unicode(artist).encode('utf-8'),
                   't':unicode(track).encode('utf-8'),
                   'b':unicode(album).encode('utf-8'),
                   'l':length,
                   'n':trackno,
                   'm':mbid }
        debug("Now playing", "%s..." % self)
        debug("Now playing", values)
        data = urllib.urlencode(values)
        req = urllib2.Request(self.now_url, data)
        try:
            response = urllib2.urlopen(req)
        except BadStatusLine:
            print "Bad status line"
            return False
        result = response.read()
        debug("Now playing", result.strip("\n"))

        if result.strip() == "OK":
            return True
        elif result.strip() == "BADSESSION":
            if inner_call == 3:
                raise SessionError('Invalid session')
            try:
                self.connect(self.user, self.password, False, self.client)
                self.now_playing(artist, track, album, length, trackno, mbid,
                        inner_call)
                inner_call += 1
            except:
                raise SessionError('Invalid session')
        else:
            return False

    def submit(self, artist, track, time, source='P', rating="", length="",
            album="", trackno="", mbid="", autoflush=False):
        """Append a song to the submission cache. Use the 'flush()' method to
        send the cache to the server. You can also set "autoflush" to True.
     
        From the Audioscrobbler protocol docs:
        -----------------------------------------------------------------------
     
        The client should monitor the user's interaction with the music playing
        service to whatever extent the service allows. In order to qualify for
        submission all of the following criteria must be met:
     
        1. The track must be submitted once it has finished playing. Whether it
            has finished playing naturally or has been manually stopped by the
            user is irrelevant.
        2. The track must have been played for a duration of at least 240
            seconds or half the track's total length, whichever comes first.
            Skipping or pausing the track is irrelevant as long as the
            appropriate amount has been played.
        3. The total playback time for the track must be more than 30 seconds.
            Do not submit tracks shorter than this.
        4. Unless the client has been specially configured, it should not
            attempt to interpret filename information to obtain metadata
            instead of tags (ID3, etc).
     
        artist: Artist name
        track: Track name
        time: Time the track *started* playing in the UTC timezone (see 
                datetime.utcnow()).
                Example: int(time.mktime(datetime.utcnow()))
        source: Source of the track. One of:
                'P': Chosen by the user
                'R': Non-personalised broadcast (e.g. Shoutcast, BBC Radio 1)
                'E': Personalised recommendation except Last.fm (e.g.
                     Pandora, Launchcast)
                'L': Last.fm (any mode). In this case, the 5-digit Last.fm
                     recommendation key must be appended to this source ID to
                     prove the validity of the submission (for example,
                     "L1b48a").
                'U': Source unknown
        rating: The rating of the song. One of:
                'L': Love (on any mode if the user has manually loved the
                     track)
                'B': Ban (only if source=L)
                'S': Skip (only if source=L)
                '':  Not applicable
        length: The song length in seconds
        album:  The album name
        trackno:The track number
        mbid:   MusicBrainz Track ID
        autoflush: Automatically flush the cache to AS?

        Returns True on success, False if something went wrong.
        """
        source = source.upper()
        rating = rating.upper()
        if source == 'L' and (rating == 'B' or rating == 'S'):
            raise ProtocolError("You can only use ration 'B' or 'S' on source"
                    " 'L'. See the docs!")
        if source == 'P' and length == '':
            raise ProtocolError("Song length must be specified when using 'P'"
                    " as source!")
        if not isinstance(time, int):
            raise ValueError("The time parameter must be of type int (unix "
                    "timestamp). Instead it was %s" % time)

        self.submit_cache.append({ 'a': unicode(artist).encode('utf-8'),
            't': unicode(track).encode('utf-8'),
            'i': time,
            'o': source,
            'r': rating,
            'l': length,
            'b': unicode(album).encode('utf-8'),
            'n': trackno,
            'm': mbid })
        debug("Submit", self.submit_cache)
        if autoflush or len(self.submit_cache) >= self.max_cache:
            return self.flush()
        else:
            return True

    def flush(self, inner_call=False):
        """
        Sends the cached song to the server.
        inner_call: internal used variable. Don't touch!
        """
        if not self.post_url:
            raise ProtocolError("Cannot submit without having a valid post-URL."
                    " Did you login?")
        values = {}
        for i, item in enumerate(self.submit_cache):
            for key in item:
                values["%s[%d]" % (key, i)] = item[key]

        values['s'] = self.session_id

        debug("Flush", "%s flush..." % self)
        debug("Flush", values)
        data = urllib.urlencode(values)
        try:
            req = urllib2.Request(self.post_url, data)
        except BadStatusLine, err:
            print err
            return False

        response = urllib2.urlopen(req)
        lines = response.read().split("\n")
        debug("Flush", lines)

        if lines[0] == "OK":
            self.submit_cache = []
            return True
        elif lines[0] == "BADSESSION":
            if not inner_call:
                self.connect(self.user, self.password, False, self.client)
                self.flush(inner_call=True)
            else:
                raise Warning('Infinite loop prevented')
        elif lines[0].startswith('FAILED'):
            self.handle_hard_error()
            raise BackendError("Submission to server failed. Reason: %s" %
                    lines[0])
        else:
            self.handle_hard_error()
            return False
        return False

if __name__ == "__main__":
    s1 = Scrobbler()
    s2 = Scrobbler("http://89.16.177.55")
    s1.connect("user", "password_hashed")
    s2.connect("user2", "password2", True)
    scrobblers = (s1, s2)
    print [s.now_playing('Front Line Assembly', 'Unleashed', length=317) for
            s in scrobblers]
    for s in scrobblers:
        print s.submit('Front Line Assembly',
             'Unleashed',
             1192374052+(5*60),
             source='P',
             length=317
             )
        print s.flush()

