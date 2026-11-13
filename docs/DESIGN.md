# ULTRON design system (desktop window and phone app)

Surface type: **Operate**. People use ULTRON to get things done, so familiar controls and clear states outrank
decoration. The one expressive element is the blue orb the operator chose; everything around it is quiet.

## Principles
- One typeface: **IBM Plex Sans** (SIL OFL, bundled). **IBM Plex Mono** only for real data: pairing codes,
  fingerprints, addresses, the `bin/ultron` command. No ALL-CAPS labels, no eyebrows, no emoji or glyphs as icons.
- One accent (the brand blue) for actions, selection and state only. Red means "microphone live" or "destructive",
  nothing else. Amber/green are status, never decoration.
- Every control shows hover, pressed, keyboard focus, disabled (and checked where it toggles). Focus is a 2 px
  ring, never colour alone.
- Empty and error screens say what happened and what to do next, in the user's words, with the action beside it
  (Try again, Allow, the command to run). Errors do not apologise.
- Controls are named for what they do and keep the same name through the flow ("Start voice" / "Stop voice").
- Motion answers an action or shows state (the orb, the listening ring, a skeleton while loading). No entrance
  choreography. Colour is chosen from the use scene: the window is dark (over the orb), the phone follows the system.

## Desktop (PySide6): `desktop/app/ultron_desktop/theme.py`, `icons.py`, `fonts.py`
- Type scale (px): 12 secondary, 13 body, 15 panel title, 20 card title. Radius 10 panels, 8 controls. 4 px spacing.
- Icons are drawn once (24 px grid, 1.75 px round strokes) and tinted per state: phone, memory, system, close, refresh.
- Toolbar toggles are icon + sentence-case label (Phone, Memory, System) with tooltips and accessible names.
- Every panel opens with the same header (title, optional action, close button).
- Captions (YOU / ULTRON) sit on a dark scrim so they stay legible over the orb; the box grows with the text.
- A "cannot reach its backend" card with Try again replaces a silent failure.

## Phone (Compose, Material 3): `android/.../ui/Theme.kt`
- Material 3 colour roles for brand blue, **designed dark and light** schemes (text contrast >= 4.5:1), shapes and
  a Plex type scale in sp (follows the system font size; checked at 1.3x).
- Material navigation bar, one FAB (New reminder), snackbars for transient feedback with **Undo**, Material dialogs
  only for decisions that must interrupt, edge-to-edge with window insets (bars and keyboard).
- Hold-to-talk is a 56 dp circular button; its ring pulses only while the microphone is live.
- Failed message: error line with Try again (it resends, never duplicates). Loading reminders: a skeleton, not a spinner.

## Sources and what was not used
Guidance read (not installed, not run): Anthropic `frontend-design` skill, Impeccable (`craft-floor`, `operate`,
`android` references), and the taste skill (a marketing-site skill; only its principles of plain copy, one system and
quiet micro-interactions were applicable). Impeccable's launcher downloads and runs a binary on first use and the
taste skill ships a stop-hook gate script that needs Node and Chrome; neither was executed.
Fonts: IBM Plex, (c) IBM Corp., SIL Open Font License 1.1 (`desktop/app/ultron_desktop/fonts/OFL.txt`,
`android/FONT-LICENSE-OFL.txt`).

## Verified / not verified
Rendered and looked at: desktop main, offline, phone panel (offscreen, software GL); Android pair, confirm, chat, reminders,
settings in dark, plus settings in light at 1.3x font on an emulator; the release build running on a real phone (connects).
Not checked: tablet/foldable layouts, TalkBack, reduced-motion settings, a high-contrast mode, the desktop on a real GPU
and Wayland, hold-to-talk on the phone.
