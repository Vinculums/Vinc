# Vinc Book, Part 0: scroll page brief

--------------------------------------------------
1. WHAT THIS PAGE IS
--------------------------------------------------

A reading page for one chapter of the Vinc book.

The page has two halves. One half carries the chapter's prose.
The other half carries a single drawing that is built up,
piece by piece, as the reader scrolls.

The drawing is not an illustration placed next to the text.
It is the same explanation, shown instead of said.
A reader who looks only at the drawing should be able to
follow the argument. A reader who reads only the text should
not feel that anything is missing.

Build one chapter per page. Start with Chapter 1.

--------------------------------------------------
2. THE THREE UNITS
--------------------------------------------------

PART is navigation only.
    Part 0 holds chapters 1 to 9. It appears in the
    progress indicator and the chapter list. It never
    triggers motion.

CHAPTER is the canvas.
    One chapter, one drawing surface. The drawing that
    exists at the end of a chapter is the drawing that
    was built across that chapter's sections. The canvas
    is cleared only at a chapter boundary.

SECTION is the scroll step.
    A section is one proposition and one visual change.
    This is the only unit where anything moves.

Chapter 1 has 7 sections plus an opening, so the page has
8 scroll steps. Other chapters range from 7 to 10.

--------------------------------------------------
3. LAYOUT
--------------------------------------------------

Text on one side, canvas on the other.

The text column scrolls normally. The canvas is pinned and
does not move while the reader is inside a chapter.

Which side the text sits on is fixed for the whole chapter
and alternates between chapters: chapter 1 text left,
chapter 2 text right, chapter 3 text left, and so on.

Do not move the canvas across the screen inside a chapter.
The whole point of the format is that the drawing persists;
sliding it around destroys that reading.

Below roughly 900px the two halves stack. The canvas sits
above its text and stays sticky at the top of the viewport
while that section's text scrolls under it.

Each text step contains:

    the chapter and section number
    a headline, at most eight words
    one to three short paragraphs
    at most one sentence set apart as the claim

The claim sentence is the one the drawing is illustrating.
There is never more than one per step.

--------------------------------------------------
4. HOW THE CANVAS CHANGES
--------------------------------------------------

Marks are added, moved, dimmed or connected. Marks are not
replaced. When a reader arrives at section 5, everything
drawn in sections 1 to 4 is still on screen unless the
argument required it to be dimmed or removed.

The change happens on the same scroll position that swaps
the text. Text and drawing move as one event, not as two
animations that happen to overlap.

Four operations, and no others:

    add        a new mark appears
    connect    a line is drawn between existing marks
    dim        a mark drops to its recede value
    label      a name attaches to a mark or a line

"Remove" is not on the list. If something must leave the
picture, dim it and leave it there. The argument is
usually that the thing persisted, not that it vanished.

Scrolling back must undo the change. A reader who scrolls
up two steps sees the drawing as it was two steps ago.

--------------------------------------------------
5. VISUAL LANGUAGE
--------------------------------------------------

This is taken from the product's own specification and is
not open to reinterpretation.

Monochrome. One ink on a paper field, plus one focus colour.
Hierarchy comes from opacity and line weight, never from hue.

The focus colour means "the thing this section is about"
and nothing else. It is never used for a category, never
used on a line, and appears at most once per chapter.
Prefer once per chapter over once per section: a page where
everything is highlighted has highlighted nothing.

Shape carries type:

    concept                 circle
    record or episode       hexagon
    bridge                  diamond
    domain or container     circle with a ring

Size means importance within the current picture, scaled by
the square root of whatever is being counted. Never linear.
One hub at linear scale eats the frame.

Every line is a hairline. Weight is expressed as ink density
in three steps, not as thickness. Lines curve slightly rather
than running dead straight, so that crossings stay readable.
Arrowheads appear only on directed relations and sit just
outside the target mark rather than at a fixed percentage
along the line.

When something is de-emphasised it fades toward the
background rather than toward grey, and it fades to fixed
values:

    marks       0.40
    lines       0.12
    text        0.35

Marks stop at 0.40 so that the surrounding picture still
reads as context. Lines drop further because lines are the
main source of visual noise.

Annotations are permitted and should look written rather
than typeset, but they are one ink and one weight. Do not
scatter marker-style flourishes, highlight boxes, sticky
notes or coloured circles. The drawing is a diagram someone
made carefully, not a whiteboard someone attacked.

--------------------------------------------------
6. MOTION
--------------------------------------------------

Animate opacity, transform and stroke-dashoffset only.
Nothing that forces layout.

A mark appearing: fades in and scales up slightly from its
own centre, over about 300ms.
A line being drawn: grows from source toward target by
stroke-dashoffset, over about 500ms, starting about 150ms
after the marks it connects have settled.
A mark dimming: opacity only, over about 200ms.

Two easing curves total. One for entering, one for leaving,
and leaving is shorter than entering. No springs, no
stiffness, no damping, no bounce.

Where several marks appear at once, stagger them by index
rather than firing them together, but keep the whole group
under 400ms.

Under `prefers-reduced-motion` the drawing still changes.
What disappears is the easing, not the effect. Marks appear
at final opacity, lines appear at full length, dimming is
instant. Never suppress the change itself, because the
change is the content.

Nothing loops. Nothing plays on a timer. Every visual change
is caused by the reader scrolling.

--------------------------------------------------
7. CHAPTER 1, EVERY BEAT
--------------------------------------------------

Chapter title: How Humanity Learned to Carry Memory Forward

Beat 0, opening.
    TEXT      The chapter title, and the question the
              chapter answers: was humanity advanced by
              discovering tools, or by sharing them?
    CANVAS    One mark, alone, near the centre. Nothing
              else on the surface.

Beat 1, from one bone to a spaceship.
    TEXT      A discovery by one being is not yet a
              civilisation. What matters is that the others
              watched, copied, and eventually used it too.
    CLAIM     A personal discovery becomes a collective
              capability.
    CANVAS    Five smaller marks appear around the first
              one. Lines are drawn outward from the centre
              mark to each of them.

Beat 2, tools turn individual capability into collective.
    TEXT      One person makes fire. Teaching a second
              person changes everything, because the second
              can teach a third. The capability crosses
              generations.
    CANVAS    Each of the five grows its own outward line
              to a new mark. The picture stops being a star
              and starts being a chain running outward.

Beat 3, memory moves beyond the individual.
    TEXT      An individual's experience disappears when
              the individual does. A record does not.
    CLAIM     Memory moves beyond individual ownership.
    CANVAS    The original centre mark dims to 0.40. Its
              outgoing lines stay at full ink. The record
              outlives the person who made it.

Beat 4, we interpret the same information differently.
    TEXT      People are not machines that store
              information. The same event reads differently
              depending on what the reader already carries.
              Historians disagree about the same century.
              Readers disagree about the most influential
              artist of the twentieth.
    CANVAS    A new mark at the top. Three lines descend
              from it to three marks of visibly different
              shape. They do not converge again. The
              divergence stays open at the bottom.

Beat 5, what we share is more than information.
    TEXT      What passes between people is not the data
              but the reading of it. One person compares
              it with their own memory, agrees, objects,
              or reads it another way entirely.
    CANVAS    Lines are drawn between the three
              interpretation marks. A new mark forms where
              they meet, labelled as a question rather
              than as an answer.

Beat 6, memory needs relationships.
    TEXT      Having many memories is not the same as
              understanding more. Which record supports
              another? Which claim contradicts another?
              When the relationships are gone, you have
              plenty of data and still start over.
    CLAIM     Relationships are what carries the meaning.
    CANVAS    The lines take labels: supports, contradicts,
              derives from. Then every mark dims to 0.40
              while the lines stay at full ink, so the
              picture reads as a network of relations
              rather than a set of things.

Beat 7, the tool has changed.
    TEXT      Cave walls, clay tablets, paper, libraries,
              the internet. Now a new kind of reader has
              appeared, one that does not only read stored
              documents but combines them, uses other
              tools, and feeds the result into its next
              task.
    CLAIM     Memory needs a user.
    CANVAS    A mark of a shape not yet used attaches to
              the network and draws lines toward itself.
              This is the only mark in the chapter that
              carries the focus colour.

Closing.
    A single line, no drawing change, leading to chapter 2.

--------------------------------------------------
8. TYPE AND COPY
--------------------------------------------------

One or two typefaces. If two, make them obviously different
rather than two neighbouring sans faces.

Body text under 80 characters per line. The book's prose
runs in very short paragraphs, often a single line, and that
rhythm is the author's, so do not merge them into blocks.

Section numbers are legitimate structure here, because a
book's chapters genuinely are a sequence. Set them small and
quiet. Do not add tracked-out all-caps eyebrow labels above
headings, do not join metadata with middle dots, and do not
append arrows to link text.

Copy comes from the book. Do not write marketing sentences
into a chapter. Do not add a call to action at the end of a
chapter.

--------------------------------------------------
9. WHAT NOT TO BUILD
--------------------------------------------------

No accent colour marking a category, a vendor, or a product.
No coloured boxes drawn around parts of the diagram.
No decorative hand-drawn texture, tape, pins or paper edges.
No parallax, no scroll-jacking, no snap-to-section.
No progress ring, no percentage read, no time-to-read badge.
No cards. The text column is prose, not a stack of tiles.
No animation that plays without the reader causing it.
No second colour axis anywhere on the page.

--------------------------------------------------
10. BUILD ORDER
--------------------------------------------------

1. Chapter 1, all eight beats, one page, desktop only.
   Get the canvas persistence right before anything else.
2. Reduced motion and keyboard scrolling.
3. Mobile stacking.
4. Chapter 7, which is the hardest case: ten beats, and
   its drawing is a hub with an agent at the centre, so it
   is the chapter that will show whether the format holds
   when the picture gets dense.
5. The remaining seven chapters of Part 0.

Stop after step 1 and show it before continuing.
