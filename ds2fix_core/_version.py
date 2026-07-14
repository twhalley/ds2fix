# Single source of truth for the ds2fix version. Everything that shows a version — the in-game menu
# label + the top-right overlay (exe/tank patches), the GUI title, and the CLI --version — derives
# from this. Bump it here, tag the release to match, and every surface updates automatically.
#
# NOTE: the in-game MENU label lives in a fixed 17-char exe slot ("$MSG$Version - %S"), so
# "ds2fix <version>" must be <= 17 chars, i.e. the version must be <= 5 chars (e.g. "0.1.2", "1.0").
# Longer versions still show in full on the gameplay overlay; the menu label just drops to "ds2fix".
__version__ = "0.1.3"
