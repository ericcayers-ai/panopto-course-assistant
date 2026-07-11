# Content style

The voice: precise, plain, a little dry. A tool written by someone who
understands the domain, not a landing page. Every string in `index.html` and
`app.js` follows the rules below.

## Voice

**Name the mechanism, not the benefit.** The reader can judge the benefit; only
we know the mechanism.

> Built from your review deck.
> ~~Your revision cockpit.~~

**State a caveat once**, where a reader would actually wonder about it — not
defensively on every card. "These run locally and need no AI model" belongs in
the Study panel intro, not repeated on the quiz, the glossary, and the guide.

**No rhetorical questions. No exclamation points** outside a genuine failure.
No "simply", "just", "easily", "powerful", "seamless".

**Sentence case** for headings and buttons ("Export subtitles", not "Export
Subtitles"). Proper nouns keep their capitals: NotebookLM, Anki, Notion, Moodle,
Panopto, Ollama, Markdown.

**Second person, present tense.** "Choose a destination", not "The user should
choose a destination" and not "This will let you choose a destination".

## Buttons and labels

- A button says what it does: `Export subtitles…`, `Start quiz`, `Reorganize`.
- A trailing `…` means "this opens a dialog before anything happens". Use it
  exactly when that is true.
- Never label a button with only an icon unless it also carries `.sr-only` text.

## Numbers and units

- Digits for all quantities: `2 pages`, `10 questions`, `about 1 GB`.
- `about 1 GB`, not `~1GB`. Space before the unit.
- Ranges use an en dash: `2–3 sentences`.

## Errors

Every error reaches the user through one function:

```js
toastError(e);          // a toast
out.textContent = errorText(e);   // an inline result pane
```

`errorText()` reads the `{error: {message, category}}` envelope every backend
integration now returns (see `app/errors.py`) and appends a category-specific
next step — "Check your connection and try again" for `network`, and so on.

Consequences:

- **Never** write `"Error: " + e.message` at a call site. The prefix and the
  hint are the formatter's job.
- **Never** invent a new toast kind. The four are `ok`, `warn`, `err`, `info`,
  and each has a matching class in `style.css`. (`"error"` is not one of them —
  it silently rendered as a plain grey toast for a long time.)
- An error message says what failed, in the user's terms. `"Could not read the
  Panopto feed"`, not `"HTTP 502"`.

## Toasts

- Past tense for something that finished: `Export complete.`
- Imperative for something the user must do: `Enter a feed URL or path.`
- Present participle for something now running: `Transcribing 4 recordings…`
- One sentence. It disappears after four seconds; anything the user needs to
  keep belongs in a result pane.

## Empty states

Say what would be here and how to put it here.

> No transcripts yet. Import a Moodle course or convert some documents.

Not "Nothing to show." and not "Oops!".
