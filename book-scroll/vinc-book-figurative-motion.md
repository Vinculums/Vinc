# Addendum: figurative motion

This adds to the Part 0 brief. It does not replace it.
Everything in the original brief still holds: the persistent
canvas, the section-level scroll step, the monochrome
language, the recede values, the motion budget.

--------------------------------------------------
1. WHY THIS EXISTS
--------------------------------------------------

The current build moves marks and lines. That is correct for
the middle of a chapter, where the reader is following an
argument. It is wrong for the opening and closing, where the
book is not arguing but showing an image.

Chapter 1 opens with a bone thrown into the sky. Chapter 6
opens a light cone. Chapter 9 closes by returning to the
crowd from Chapter 1 and naming what has been happening for
nine chapters. These are the author's images. The page should
show them.

--------------------------------------------------
2. TWO REGISTERS, ONE MATERIAL
--------------------------------------------------

The canvas holds two kinds of drawing.

    DIAGRAM     marks, lines, labels. Carries the argument.
    FIGURE      a recognisable form. Carries the image.

They are drawn with the same ink, the same hairline weight,
and the same three opacity steps. A figure is never an
illustration dropped onto the canvas. It is the diagram's
own marks arranged into a recognisable shape.

Worked example, the one that started this.

    A crowd around a discovery is not a drawing of apes. It
    is nine marks arranged in a ring around a tenth. When
    the chapter ends and the crowd returns, it is still nine
    marks in the same nine positions. What changed is the
    shape each mark takes. Nobody is redrawn. The ring is
    the constant, and the ring is what the argument was
    about.

    A bone is four strokes. A vessel is the same four
    strokes, rotated and rescaled. The reader sees one form
    become the other because the strokes never break.

If a figure cannot be built from the diagram's own marks, it
does not belong on this page. Do not solve it by importing an
illustration style.

--------------------------------------------------
3. WHERE FIGURES ARE ALLOWED
--------------------------------------------------

At most two per chapter. One at the opening beat, one at the
closing beat. The middle beats are diagram only.

A chapter may have zero figures. Several should.

The reason for the cap is not budget. A page where every
section has a picture stops reading as an argument and starts
reading as a slideshow. The figure earns its force by being
the thing that bookends the diagram, and that force is
divided by however many times it is used.

Part 1 and Part 2 get no figures at all. See section 5.

--------------------------------------------------
4. HOW A MORPH IS BUILT
--------------------------------------------------

Two forms that morph into each other are one path set with
matched anchor counts. Form A and form B have the same number
of anchors in the same order, so the interpolation is a
straight tween between coordinate arrays and never a crossfade
between two images.

The tween is driven by scroll progress from 0 to 1 across the
beat, not by a timer. The reader scrubs it. Scrolling up
reverses it. Stopping halfway leaves it halfway, and the
halfway state must be legible rather than a smear, which is
the real test of whether the two forms were built from the
same skeleton.

Where a morph cannot be built this way, the fallback is not a
crossfade. The fallback is to cut the figure and use the
diagram.

Under `prefers-reduced-motion` the beat shows the end state
with no interpolation. The reader still sees the vessel; they
just never see it forming.

--------------------------------------------------
5. MOTION GRAMMAR BY PART
--------------------------------------------------

PART 0, narrative.
    Figures allowed at chapter open and close.
    Marks accumulate on a persistent canvas.
    The reader is watching a picture get built.

PART 1, pipeline.
    No figures. Nothing accumulates.
    Things enter a stage, transform, and leave.
    The canvas is a conveyor, not a page.
    The characteristic motion is a document splitting into
    chunks, a chunk becoming a point, a point landing in a
    field.

PART 2, measurement.
    No figures. No narrative motion at all.
    Only before and after, counts, and bars.
    The characteristic motion is running the same thing
    twice under two conditions and letting the two results
    sit next to each other.
    Resist making failure look dramatic. The whole point of
    Part 2 is that a failure looked normal.

PART 3, the cycle.
    One chapter, one figure, and it is a loop.
    The site bans looping animation everywhere else. This is
    the exception, and it is earned because the chapter's
    claim is that the process is circular.
    It loops three times and stops. It does not loop forever.

--------------------------------------------------
6. THE CATALOGUE
--------------------------------------------------

Each entry: the image the book invokes, and what moves.

### Part 0

CHAPTER 1, How humanity learned to carry memory forward

    OPEN, the thrown object.
    Four strokes rise and rotate. At the top of the arc the
    same four strokes settle into a vessel. Scroll-scrubbed.
    The morph happens at the apex, so a reader who stops
    scrolling stops it mid-air.

    Ring of marks around a centre, used at beat 1.
    The centre lights first, then the ring appears one mark
    at a time with a stagger.

    Stacked ground lines, for the shoulders of giants.
    Each generation draws a horizontal rule above the last
    and the viewport rises with it, so the reader is always
    at the top of a stack that is getting taller underneath.

    CLOSE, the ring returns, same count and positions, new
    mark shape. This is the only figure in the chapter that
    carries the focus colour.

CHAPTER 2, Memory in the age of AI

    OPEN, the flood.
    Documents fall and pile until the pile is a solid block
    and nothing in it is distinguishable. Then one is pulled
    out and it is unreadable out of context.

    Docking, used mid-chapter for borrowed cognition.
    A core mark holds still. A second mark docks to it, a
    line runs between them, then it undocks and leaves. A
    third mark of a different shape docks in the same place.
    The core never moves, never changes, never loses ink.
    That is the entire argument of the chapter.

    CLOSE, one core with doors around it.
    Four openings around the core, each opening in turn.
    Through every one, the same core.

CHAPTER 3, Memory changes the answer

    The distribution.
    This chapter needs no figure because its argument is
    already a picture. A wide low curve. Context marks drop
    into it one at a time and the curve narrows and shifts
    with each one. The claim and the drawing are the same
    object.

    Two runs, for the determinism section.
    The same input traced twice. One path wobbles and lands
    somewhere different on each pass. The other lands on the
    same point every pass. Let the reader trigger the rerun,
    because seeing it land identically twice is the proof.

CHAPTER 4, What should we remember

    OPEN, the cut.
    An undifferentiated mass. A single line passes through
    it. The pieces separate and each takes a type shape.
    Continuous becomes discrete. This is the chapter's
    sentence about naming being a way of cutting the world,
    drawn exactly.

    Everything at once, for the storing-everything section.
    Every mark at full ink. A search sweep passes over and
    finds nothing, because nothing is distinguishable from
    anything else.

    Concept against record.
    One mark holds position. Below it, record marks
    accumulate along a time axis, each one stamped. The
    concept does not move while the records pile up.

CHAPTER 5, Memory needs a grammar

    OPEN, three hands.
    Three writers record the same event. Three different
    shapes, arrows in three different directions. The picture
    is visibly incoherent, and it should look wrong rather
    than look busy. Then a grammar is applied and the three
    collapse to one.

    Rule strength as line weight.
    The same diagram drawn three times: solid frame for
    schema-fixed, dashed for this-graph-decides, light for
    varies-by-topic. Same content, three weights.

    The gate.
    A malformed mark bounces off. A well-formed but
    meaningless mark passes. Then a human mark stops it. The
    checker does not decide meaning, and this shows exactly
    where it stops being able to.

CHAPTER 6, Memory needs space

    OPEN, the cone.
    A point on a time axis. A cone opens upward and downward
    from it. Marks inside light up, marks outside drop to
    0.40. The book names this explicitly as a borrowed lens
    rather than an implementation, so the drawing should not
    look like a physics figure. Hairlines, no shading.

    Two clocks.
    Two axes. One record plotted at 2020 on one and 2026 on
    the other, and the diagonal between them drawn. Then a
    second record with the two times equal, sitting on the
    diagonal. The difference between the two is the point.

    Layout determinism.
    The same graph laid out three times. Under a force
    simulation it lands somewhere different each pass. Under
    the deterministic layout, three identical results. The
    reader triggers each pass.

    CLOSE, one graph, three windows.
    The same marks rearrange into three arrangements. The
    count never changes. No mark enters and none leaves,
    which is the whole claim about view and source.

CHAPTER 7, The being that uses memory

    No opening figure. This chapter is already a diagram and
    it is the closest thing in the book to a hub picture.

    Two readers.
    Two identical marks side by side, given different reading
    sets, producing visibly different output. The marks are
    the same. Only what they read differs.

    The loop closing.
    Read arrow from graph into agent early in the chapter.
    Write arrow from agent back into graph at the end. The
    ring only closes on the last beat.

    Failure constraining the next run.
    A traversal takes a branch and fails. A record attaches
    to the role. The next traversal reaches the same fork and
    does not take that branch.

CHAPTER 8, Can we trust memory

    OPEN, the same path.
    The chain from information down to action, drawn once in
    ink. Then the top mark is marked wrong and the same chain
    is traced again at the same speed along the same path.
    Nothing about the path changes. Only the value does.

    Present but unreachable.
    Two marks, one true and one false, with no edge between
    them. A traversal reaches the false one and cannot reach
    the true one. Then one edge is drawn and the traversal
    reaches it. The problem was never that the information
    was missing.

    Ageing.
    A record's ink fades along a time axis while a validity
    bracket around it closes. It does not disappear. It stops
    being current, which is a different thing.

    Claim with and without evidence.
    A claim mark alone. Then the same claim with four small
    attachments: evidence, tool, target, time.

CHAPTER 9, What we deliberately do not do

    OPEN, the boundary.
    The graph grows outward and stops at a line. The line is
    drawn deliberately before the growth reaches it, not
    revealed at the moment of collision. Refusal is a
    decision, not an accident.

    The filled blank.
    A record with an empty slot. A plausible value fills it
    and the mark's ink changes to something visibly not the
    same substance. Then it empties again. The empty state
    should read as intact, not as broken.

    CLOSE, the return.
    This is the payoff for all of Part 0. The ring from
    Chapter 1 beat 1 comes back, same count, same positions.
    Then every edge in the accumulated picture is drawn at
    once, in a single sweep, and the word vinculum appears.
    This is the largest single motion on the site and it
    happens exactly once.

### Part 1, sampled

    How meaning becomes space.
    A document splits into chunks. Each chunk becomes a point
    in a field. Near points cluster. Then the model changes
    and every point in the field moves at once. One motion
    carries the entire cost-of-re-embedding argument.

    How memory becomes material.
    A query goes in. What comes back is a stack of excerpts,
    not a sentence. Resist animating a sentence being
    written; the chapter's claim is that a sentence is not
    what comes back.

    What time exists in memory.
    The two clocks again, in a pipeline register rather than
    a narrative one.

    How causal relationships become space.
    The cone again, same treatment as Chapter 6, so that the
    reader recognises it as a return rather than as a new
    idea.

### Part 2, sampled

    Failures that look like success.
    A metric bar fills to a healthy number. Behind it, the
    fixture it measured is visibly empty. Both are true at
    the same time and neither is animated dramatically. The
    flatness is the point.

    Measurement needs conditions.
    The same measurement run twice under two conditions,
    producing two numbers, side by side, with the conditions
    labelled and the numbers not editorialised.

    Tool efficiency in call count.
    Calls drawn as literal marks in a row. Seven, then two.
    No bar chart needed when the number is small enough to
    count.

### Part 3

    Memory is a cycle.
    Record, structure, search, interpret, use, measure,
    decide, record again. Marks from the earlier chapters
    take their places on the ring. The loop runs three times
    and stops on the first position.

--------------------------------------------------
7. RIGHTS AND LIKENESS
--------------------------------------------------

The film Chapter 1 discusses is a copyrighted work. Reference
the idea in this page's own line language. Do not recreate
frames, do not draw a recognisable version of the object the
apes gather around, and do not build the crowd as costumed
figures. Abstract marks only. The book names the film in
prose, which is commentary, and the drawing should stay
further from the source than the prose does.

Chapter 1 names four living or recently living musicians as
an example of differing interpretation. Do not draw
identifiable people anywhere on this page. Those three marks
stay unlabelled and unfaced.

The Newton line is from 1675 and is quoted in the book. It
can appear as set text.

--------------------------------------------------
8. WHAT THIS DOES NOT LICENSE
--------------------------------------------------

No character design. No faces. No costumes. No scenery.
No perspective drawing, no vanishing points, no horizon
    lines except the stacked rules in Chapter 1.
No second ink, no gradient, no texture, no shading.
No motion that plays without the reader scrolling, except
    the single loop in Part 3.
No figure in the middle beats of a chapter.
No figure anywhere in Part 1 or Part 2.
No sound.

The figures are permitted because the book invokes them, not
because motion is nice to look at. Any figure that cannot be
traced to a sentence in the chapter it sits in should be cut.

--------------------------------------------------
9. BUILD ORDER
--------------------------------------------------

1. Chapter 3, the distribution. It is the strongest case and
   it needs no figure at all, so it tests whether the new
   motion grammar works before any morph is built.
2. Chapter 1, the thrown object and the ring. This is the
   first real morph and it will decide whether the matched
   anchor approach holds.
3. Chapter 9, the return. It depends on Chapter 1's ring
   existing, and building it early keeps the two honest.
4. The remaining Part 0 chapters.
5. One Part 1 chapter and one Part 2 chapter, to confirm the
   grammar actually changes between parts rather than
   drifting back into Part 0's register.

Stop after step 2 and show both.
