import os
try:
    from hashlib import md5
except ImportError:
   import md5
from os import path


__all__ = ('Path', 'Writer', 'file_md5')


def file_md5(filename):
    h = md5()
    f = open(filename, 'rb')
    try:
        while True:
            # 128 is the md5 digest blocksize
            data = f.read(128*10)
            if not data:
                break
            h.update(data)
        return h.digest()
    finally:
        f.close()

class Path(unicode):
    """Helper representing a filesystem path that can be "bound" to a base
    path. You can then ask it to render as a relative path to that base.
    """

    def __new__(self, *parts, **kwargs):
        base = kwargs.pop('base', None)
        if kwargs:
            raise TypeError()
        self.base = base
        abs = path.normpath(path.abspath(path.join(*parts)))
        return unicode.__new__(self, abs)

    @property
    def rel(self):
        base =  self.base or os.getcwd()
        if not hasattr(path, 'relpath'):
            # Python < 2.6 doesn't have relpath, and I don't want
            # to bother with a wbole bunch of code for this. See
            # if we can simply remove the prefix, and if not, 2.5
            # users will have to live with the absolute path.
            if self.path.startswith(base):
                return self.path[len(base)+1:]
            return self.abs
        return path.relpath(self, start=base)

    @property
    def abs(self):
        return self

    def exists(self):
        return path.exists(self)

    @property
    def dir(self):
        return Path(path.dirname(self), base=self.base)

    def hash(self):
        return file_md5(self)


class Writer():
    """Helps printing messages to the output, in a very particular form.

    Supported are two concepts, "actions" and "messages". A message is
    always the child of an action. There is a limited set of action
    types (we call them events). Each event and each message may have a
    "severity". The severity can determine how a message or event is
    rendered (if the terminals supports colors), and will also affect
    whether a action or message is rendered at all, depending on verbosity
    settings.

    If a message exceeds it's action in severity causing the message to
    be visible but the action not, the action will forcably be rendered as
    well. For this reason, the class keeps track of the last message that
    should have been printed.

    There is also a mechanism which allows to delay printing an action.
    That is, you may begin constructing an action and collecting it's
    messages, and only later print it out. You would want to do this if
    the event type can only be determined after the action is completed,
    since it often indicates the outcome.
    """

    # Action types and their default levels
    EVENTS = {
        'info': 'info', 'mkdir': 'default', 'updated': 'default',
        'unchanged': 'default', 'skipped': 'warning', 'created': 'default',
        'exists': 'default', 'failed': 'error',}

    # Levels and the minimum verbosity required to show them
    LEVELS = {'default': 2, 'warning': 1, 'error': 0, 'info': 3}

    # +2 for [ and ]
    # +1 for additional left padding
    max_event_len = max([len(k) for k in EVENTS.keys()]) + 2 + 1

    class Action(dict):
        def __init__(self, writer, *more, **data):
            self.writer = writer
            self.messages = []
            self.is_done = False
            self.awaiting_promotion = False
            dict.__init__(self, {'text': '', 'status': None, 'severity': None})
            self.update(*more, **data)

        def __setitem__(self, name, value):
            if name == 'severity':
                assert value in Writer.LEVELS, 'Not a valid severity value'
            dict.__setitem__(self, name, value)

        def done(self, event, *more, **data):
            assert event in Writer.EVENTS, 'Not a valid event type'
            self['event'] = event
            self.update(*more, **data)
            self.writer._print_action(self)
            if self in self.writer._pending_actions:
                self.writer._pending_actions.remove(self)
            self.is_done = True

        def update(self, text=None, severity=None, **more_data):
            if text:
                self['text'] = text
            if severity:
                self['severity'] = severity
            dict.update(self, **more_data)

        def message(self, message, severity='info'):
            """Print a message belonging to this action.

            If the action is not yet done, this will be added to
            an internal queue.

            If the action is done, but was not printed because it didn't
            pass the verbosity threshold, it will be printed now.

            By default, all messages use a loglevel of 'info'.
            """
            is_allowed = self.writer.allowed(severity)
            if not self.is_done:
                if is_allowed:
                    self.messages.append(message)
            elif is_allowed:
                if self.awaiting_promotion:
                    self.writer._print_action(self, force=True)
                self.writer._print_message(message)

        @property
        def event(self):
            return self['event']

    def __init__(self, verbosity=LEVELS['default']):
        self._current_action = None
        self._pending_actions = []
        self.verbosity = verbosity

    def action(self, event, *a, **kw):
        action = Writer.Action(self, *a, **kw)
        action.done(event)
        return action

    def begin(self, *a, **kw):
        action = Writer.Action(self, *a, **kw)
        self._pending_actions.append(action)
        return action

    def message(self, *a, **kw):
        self._current_action.message(*a, **kw)

    def finish(self):
        """Close down all pending actions that have been began(), but
        are not yet done.

        Not the sibling of begin()!
        """
        for action in self._pending_actions:
            if not action.is_done:
                action.done('failed')
        self._pending_actions = []

    def allowed(self, severity):
        """Return ``True`` if mesages with this severity pass
        the current verbosity threshold.
        """
        return self.verbosity >= self.LEVELS[severity]

    def _print_action(self, action, force=False):
        """Print the action and all it's attached messages.
        """
        sev = action['severity']
        if not sev:
            sev = self.EVENTS[action.event]

        if force or self.allowed(sev):
            self._print_action_header(action)
            for m in action.messages:
                self._print_message(m)
            action.awaiting_promotion = False
        else:
            # Indicates that this message has not been printed yet,
            # and is waiting for a dependent message that needs to
            # be printed to trigger it.
            action.awaiting_promotion = True
        self._current_action = action

    def _print_action_header(self, action):
        text = action['text']
        status = action['status']
        if isinstance(text, Path):
            # Handle Path instances manually. This doesn't happen
            # automatically because we haven't figur out how to make
            # that class represent itself through the relative path
            # by default, while still returning the full path if it
            # is used, say, during an open() operation.
            text = text.rel
        if status:
            text = "%s (%s)" % (text, status)
        print ("%"+str(self.max_event_len)+"s %s") % (
            "[%s]" % action['event'], text)

    def _print_message(self, message):
        print " "*(self.max_event_len+1) + u"- %s" % message
