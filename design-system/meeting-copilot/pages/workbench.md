# Workbench Page Override

The Workbench is a desktop productivity tool used during a meeting. Use a quiet, content-first layout with a neutral graphite base and restrained teal/amber status colors. Keep the meeting transcript and the current AI suggestion visible before secondary diagnostics or settings.

## Interaction Rules

- The first screen exposes only three primary actions: `开始会议`, `导入录音`, and `历史记录`.
- File import always shows an explicit busy state and disables duplicate submission.
- The input remains keyboard accessible even though the native file chooser is triggered by a visible button.
- Imported recordings open the same V2 review route as microphone meetings; do not create a second review surface.
- Preserve visible focus rings and use Lucide icons with text for unfamiliar commands.

## Responsive Rules

- At widths below 760px, the action group uses two equal columns and allows the third action to wrap without horizontal scrolling.
- Do not let the provider status button, import button, or start button shrink text into an unreadable ellipsis.
- Async operations show a spinner and status text; no blank or frozen state is acceptable.

## Palette Direction

- Background: neutral charcoal or warm white according to the existing application mode.
- Primary action: restrained teal/blue-green.
- Attention: amber.
- Failure: red.
- Avoid a purple-dominant gradient or decorative cards/orbs; this is an operational meeting tool, not a marketing hero.
