"""The position of one player, and the transport that moves it, for whichever screen shows it.

The player (MpvWidget) owns libmpv: the async scrub lane with its newest-target and ordering
rules, load generations, decoder safeguards, frame maths and drawing. A Playhead owns what a
screen needs on top of that: which frame is *the* position, and whether it is only requested.

- `frame` is the position. While a scrub settles it is the frame asked for (`target`), and
  late reports of earlier targets are ignored, so a note, bookmark or in/out point made then
  goes on the requested frame. Otherwise it follows the player's reports.
- A scrub settles when the player reports the target, or says the last scrub seek is done
  (from then its position is the truth: a file can be shorter than its recorded frame count).
  A timer is the backstop for a player that never answers.
- Seeks and steps behind a pending scrub go the same ordered way, from the requested frame.

Nothing here draws, formats, stores or knows about notes. `moved` and `pending_changed` tell
the screen to redraw (and save the position); `still_seek` asks it to show a frame when the
picture on screen is not the player's (missing media, no libmpv).

The player is used through this much of MpvWidget: `loaded`, `frame`, `paused`, `scrub_to`,
`seek_frame`, `step`, `set_pause`, `scrub_pending`, and the `frame_changed` and
`scrub_settled` signals. `player_shown` says whether the player is the picture on screen.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal


class Playhead(QObject):
    moved = Signal(int)              # the position changed: asked for, or reported by the player
    pending_changed = Signal(bool)   # the position became, or stopped being, only requested
    still_seek = Signal(int)         # a seek while the picture on screen is not the player's

    BACKSTOP_MS = 3000

    def __init__(self, player, player_shown: Callable[[], bool], parent: QObject | None = None):
        super().__init__(parent)
        self.player = player
        self._shown = player_shown
        self.frame = 0
        self.count = None            # frames in the current media; None until there is some
        self.target = None           # the frame asked for, while a scrub settles
        self._timer = QTimer(self, singleShot=True, interval=self.BACKSTOP_MS)
        self._timer.timeout.connect(self._settle_timeout)
        player.frame_changed.connect(self._on_player_frame)
        player.scrub_settled.connect(self._on_scrub_settled)
        self._attached = True

    @property
    def pending(self) -> bool:
        return self.target is not None

    # ---- lifecycle ---------------------------------------------------------------------
    def begin(self, count: int, frame: int | None = None, new: bool = True):
        """Media of `count` frames. `new`: a different asset, so a scrub of the previous one is
        dropped; otherwise a refresh of the same one. `frame` sets the position without moving
        the player (the caller loads the player there)."""
        self.count = count
        if new:
            self._end_settle()
        if frame is not None:
            self.frame = frame

    def stop(self):
        """Inactive for good (shutdown): drop a pending scrub and stop following the player."""
        self._end_settle()
        self.count = None
        if self._attached:
            self.player.frame_changed.disconnect(self._on_player_frame)
            self.player.scrub_settled.disconnect(self._on_scrub_settled)
            self._attached = False

    # ---- transport ---------------------------------------------------------------------
    def scrub(self, f: int):
        """Timeline press, drag and release: exact seeks that never block the GUI thread."""
        if self.count is None:
            return
        if not self._shown() or not self.player.loaded:
            self._end_settle()  # nothing on the player to wait for
            self.seek(f)
            return
        f = max(0, min(self.count - 1, int(f)))
        was = self.pending
        self.target = f
        self._timer.start()
        self.frame = f
        if not was:
            self.pending_changed.emit(True)
        self.moved.emit(f)
        self.player.scrub_to(f)

    def seek(self, f: int):
        if self.count is None:
            return
        if self.target is not None:  # behind a scrub that is still settling: same ordered path
            self.scrub(f)
            return
        f = max(0, min(self.count - 1, int(f)))
        self.frame = f
        self.moved.emit(f)
        if self._shown():
            self.player.seek_frame(f)
        else:
            self.still_seek.emit(f)

    def step(self, n: int):
        if self.target is not None:  # from the frame asked for, not wherever the player has got to
            self.seek(self.frame + n)
        elif self._shown() and self.player.loaded and abs(n) == 1:
            self.player.step(n)
        else:
            self.seek(self.frame + n)

    def toggle_play(self, looping: bool) -> bool:
        """Play / pause; at the end of a file that is not looping, play again from the top.
        False if nothing playable is loaded."""
        if not self.player.loaded:
            return False
        if self.player.paused and not looping and self.frame >= self.count - 1:
            self.seek(0)
            self.player.set_pause(False)
        else:
            self.player.set_pause(None)
        return True

    # ---- from the player -----------------------------------------------------------------
    def _on_player_frame(self, f: int):
        if self.count is None:
            return
        if self.target is not None:
            if f != self.target:
                return  # an earlier target arriving late
            self._end_settle()
        self.frame = max(0, min(self.count - 1, f))
        self.moved.emit(self.frame)

    def _on_scrub_settled(self):
        """mpv has carried out the last scrub seek: from here its position is the truth again (it can
        differ from the target only if the file is shorter than its recorded frame count)."""
        if self.target is not None:
            self._end_settle()
            if self.player.frame != self.frame:
                self._on_player_frame(self.player.frame)

    def _settle_timeout(self):
        if self.player.scrub_pending():
            self._timer.start()
            return
        self._end_settle()
        self._on_player_frame(self.player.frame)  # take the last position the player reported

    def _end_settle(self):
        self._timer.stop()
        if self.target is not None:
            self.target = None
            self.pending_changed.emit(False)
