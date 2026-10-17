"""Authored option-specific near-misses for every choice in the curriculum kits.

Rows are rung 1, 3, 4; within a row the three distractors retain source order.
These explain the *idea*, not a claim about the learner's personal motivation.
Corrections quote the kit's checked rationale. Missing authoring fails closed.
"""

from __future__ import annotations

REASONS = {
    "calc_rate": [
        (
            "Position is a measurement, but a rate compares measurements over time.|Journey "
            "duration measures time, not change per unit time.|Fuel is a quantity, not how "
            "quickly a quantity changes."
        ),
        (
            "Multiplying gives the amount lost, not the amount remaining.|Adding the loss "
            "treats draining as filling.|Dividing time by loss rate does not subtract the "
            "lost volume."
        ),
        (
            "Growing suggests positive, but the later shrinking reverses the "
            "sign.|Shrinking suggests negative, but the earlier growth has positive sign.|A "
            "counteracting change is not zero unless the queue stops changing."
        ),
    ],
    "calc_limit": [
        (
            "Substitution gives a value at a point, not approaching behaviour.|A square "
            "root reverses squaring; it does not describe approach.|A large value is not "
            "necessarily a limiting value."
        ),
        (
            "The numerator stays fixed, but the quotient does not.|The denominator grows, "
            "but its reciprocal shrinks.|An unbounded denominator makes this fraction "
            "smaller, not larger."
        ),
        (
            "The first halving is easy to picture, but halving keeps happening.|The initial "
            "bounce is a starting value, not the destination.|Never reaching a value does "
            "not prevent approaching it."
        ),
    ],
    "calc_derivative": [
        (
            "Area is associated with curves, but it is an integral, not a gradient.|A "
            "secant averages a whole interval rather than giving local gradient.|A maximum "
            "is a location on the curve, not the slope function."
        ),
        (
            "Keeping the original expression leaves the function undifferentiated.|Bringing "
            "down the power is only half the rule; the variable remains.|Halving the "
            "variable reverses neither the power nor the slope calculation."
        ),
        (
            "Twice the side gives the area gradient, but omits the side's growth "
            "rate.|Squaring the side gives area rather than change in area.|Squaring the "
            "growth rate ignores the current side length."
        ),
    ],
    "calc_rules": [
        (
            "A sum invites differentiation, but does not need the chain rule.|Term-by-term "
            "differentiation is the sum rule, not composition.|A constant has a derivative, "
            "but no changing inner function."
        ),
        (
            "The outer derivative looks right, but the inner multiplier is missing.|A minus "
            "sign belongs to differentiating cosine, not sine.|The inner multiplier is "
            "right, but sine must become cosine."
        ),
        (
            "Differentiating each factor then multiplying omits both product "
            "terms.|Differentiating sine alone leaves the changing square untreated.|Both "
            "terms are present, but the product rule adds rather than subtracts."
        ),
    ],
    "dm_sets": [
        (
            "A list looks like a collection, but repetitions are not distinct members.|A "
            "member belongs to a set but is not the collection itself.|A familiar "
            "description still needs a precise membership criterion."
        ),
        (
            "Collecting every member gives the union, not the intersection.|Removing shared "
            "members gives the symmetric difference.|Different sets can still have a shared "
            "member."
        ),
        (
            "The neither count is given directly, but is not the overlap.|Subtracting "
            "counts from the class total misses inclusion-exclusion.|Adding both subject "
            "counts counts shared learners twice."
        ),
    ],
    "dm_logic": [
        (
            "A question concerns a claim, but asserts no truth-valued "
            "statement.|Probability wording does not supply the fixed claim required "
            "here.|Naming a shape is not asserting something about it."
        ),
        (
            "Reversing all to none is stronger than finding a counterexample.|Universal "
            "failure contradicts passing, but is not necessary to negate all.|Some passing "
            "is compatible with all passing, so breaks nothing."
        ),
        (
            "A stated consequence suggests its cause, but other causes are possible.|Not "
            "proving rain does not prove its absence.|The rule concerns rain and closure, "
            "not tomorrow's opening."
        ),
    ],
    "dm_counting": [
        (
            "Alphabet size counts objects, not arrangements.|A committee chooses members, "
            "but does not order them.|Counting occurrences does not count ways to arrange "
            "objects."
        ),
        (
            "Choosing a group ignores the distinct finishing positions.|Repeated "
            "multiplication allows the same runner in several positions.|Multiplying "
            "runners by places does not enumerate ordered selections."
        ),
        (
            "Ordering the committee counts each group repeatedly.|People times places does "
            "not count the possible groups.|Allowing repeated choices admits the same "
            "person more than once."
        ),
    ],
    "alg_terms": [
        (
            "Matching coefficients catch the eye, but the letters differ.|Matching letters "
            "are insufficient when powers differ.|A constant and a variable term have "
            "different variable parts."
        ),
        (
            "Combining coefficients is right, but subtraction was treated as addition.|The "
            "subtraction on the a term does not change addition on the b terms.|Combining "
            "all numbers ignores the different variable parts."
        ),
        (
            "Combining the linear terms requires subtraction, not addition.|The negative "
            "square term must also be collected.|Adding unlike powers does not multiply "
            "variables or add exponents."
        ),
    ],
    "alg_expand": [
        (
            "The first product is right, but the other term also needs multiplying.|Adding "
            "the outside number is not distributing multiplication.|Squaring both terms "
            "separately omits the cross terms."
        ),
        (
            "The variable term is multiplied, but the constant was left unchanged.|Adding "
            "the two coefficients replaces multiplication with addition.|The magnitude is "
            "right, but multiplying a negative keeps its sign."
        ),
        (
            "The end products are right, but the middle terms are missing.|The middle "
            "coefficients have opposite signs, not matching signs.|The signs of the middle "
            "and constant products were reversed."
        ),
    ],
    "alg_solve": [
        (
            "Moving a term is convenient shorthand, but needs equal operations on both "
            "sides.|Guessing can find a solution but does not explain the balance.|Dividing "
            "one side alone changes the equality."
        ),
        (
            "Undoing the constant is needed, but its inverse operation is "
            "addition.|Removing the constant leaves a multiple of the unknown, not the "
            "unknown.|A sign change alone does not isolate the variable."
        ),
        (
            "Collecting terms is useful, but the negative constant must be moved by "
            "addition.|The inverse of subtracting the constant is adding it.|The isolated "
            "multiple still needs dividing by its coefficient."
        ),
    ],
    "alg_rearrange": [
        (
            "Substitution finds a numerical value, not a formula for a new "
            "subject.|Expanding brackets changes form but does not isolate the requested "
            "variable.|Approximating a constant does not rearrange the relationship."
        ),
        (
            "The shared factor is removed, but the other addend must be subtracted.|Undoing "
            "division requires multiplication; the factor was inverted wrongly.|Removing a "
            "multiplying factor requires division, not multiplication."
        ),
        (
            "The other velocity term must be subtracted, not added.|Division must apply to "
            "the whole difference, not just its first term.|The coefficient multiplying "
            "time must be divided out."
        ),
    ],
    "stat_collect": [
        (
            "An opinion relates to travel but is not a collected observation.|One story "
            "provides detail but not the class frequency distribution.|A guess is a number "
            "without the required observations."
        ),
        (
            "A common category can look plausible, but its frequency is below "
            "walking.|Cycling is listed, but its frequency is not the largest.|The smallest "
            "group is a category, not the modal category."
        ),
        (
            "The count rose, but the population also grew.|Changed totals prevent raw-count "
            "comparison, not proportional comparison.|Total enrolment measures size rather "
            "than the share walking."
        ),
    ],
    "stat_average": [
        (
            "The maximum is memorable, but does not represent the arithmetic mean.|Range "
            "describes separation, not the central value.|The sum is a step toward a mean, "
            "but still needs division."
        ),
        (
            "Adding and dividing finds the mean rather than the middle "
            "observation.|Averaging two central values applies to an even-sized list.|A "
            "value near the middle is not enough; the central position is exact."
        ),
        (
            "An extreme income shifts the mean, but scarcely shifts middle positions.|One "
            "observation can strongly affect a sum-based statistic.|The outlier pulls the "
            "mean upward rather than below the median."
        ),
    ],
    "stat_spread": [
        (
            "Equal averages describe centre, not how far marks vary.|Class size counts "
            "observations, not their variation.|The maximum alone leaves the lower end "
            "unknown."
        ),
        (
            "The highest mark is an endpoint, not the distance between endpoints.|The "
            "lowest mark is an endpoint, not the width of the data.|An average describes "
            "centre rather than range."
        ),
        (
            "The fastest trip is attractive, but ignores much slower trips.|Equal means do "
            "not imply equal reliability.|The shared average is not itself a reason to "
            "reject a predictable service."
        ),
    ],
    "gov_power": [
        (
            "Reporting on a court resembles scrutiny, but is not an arm exercising state "
            "power.|A complaint participates in civic life without being an arm of "
            "government.|Parties organise politics, but are not the constitutional arms."
        ),
        (
            "The executive implements laws, but does not pass bills in its executive "
            "role.|The judiciary interprets laws rather than legislating.|Officials "
            "administer policy rather than debating and passing bills."
        ),
        (
            "Making a regulation gives authority, but not independent judgment of its "
            "legality.|Writing the law differs from adjudicating a dispute under "
            "it.|Holding political office does not replace judicial review."
        ),
    ],
    "gov_law": [
        (
            "An announcement sounds official, but is not enacted legislation.|A judgment "
            "interprets existing law rather than passing a bill.|A local decision is not "
            "the parliamentary process described."
        ),
        (
            "A referendum can decide some issues, but is not the usual next parliamentary "
            "stage.|Assent comes later, not before the required parliamentary "
            "scrutiny.|Courts review lawfulness, not carry out the normal bill debate."
        ),
        (
            "Upper-house approval alone leaves the competing texts unresolved.|Lower-house "
            "approval alone does not reconcile the two versions.|Disagreement requires "
            "reconciliation, not necessarily starting the entire bill again."
        ),
    ],
    "gov_rights": [
        (
            "Rights are protected, but some allow lawful proportionate limits.|A personal "
            "favour depends on discretion, unlike a constitutional entitlement.|A manifesto "
            "promises policy rather than establishing an enforceable right."
        ),
        (
            "Employment matters, but a specific government job is not guaranteed to "
            "everyone.|Owning transport is useful, but not a universal constitutional "
            "entitlement.|Rights do not create a general exemption from taxation."
        ),
        (
            "Personal agreement concerns preference, not whether a restriction is "
            "justified.|Foreign practice is relevant context, not the constitutional test "
            "itself.|Popularity does not establish necessity or proportionality."
        ),
    ],
    "gov_accountability": [
        (
            "Publicity reports a decision but does not require answering for it.|An opinion "
            "criticises government but alone is not an accountability mechanism.|Internal "
            "party selection does not scrutinise public expenditure or legality."
        ),
        (
            "A ministry message presents its own position rather than independent "
            "scrutiny.|A partisan speech does not supply independent oversight.|A publicity "
            "campaign informs or persuades without checking accounts."
        ),
        (
            "Reviewing legality constrains a minister but does not assume the minister's "
            "role.|A judicial ruling is not an instruction to enact the decision.|Being "
            "reviewed once does not remove future legal accountability."
        ),
    ],
    "gr_sentence": [
        (
            "A subject and verb appear, but the opening conjunction makes the clause "
            "dependent.|The description contains a verb but leaves the main clause "
            "unfinished.|An action is present, but its subject is missing."
        ),
        (
            "A full stop cannot make a dependent because-clause independent.|The relative "
            "clause describes the market without a main assertion.|A finite verb still "
            "needs a subject here."
        ),
        (
            "The opening action sounds natural, but grammatically it describes the "
            "shops.|Removing the comma does not supply the missing person who was "
            "walking.|Length does not fix a modifier attached to the wrong subject."
        ),
    ],
    "gr_agreement": [
        (
            "The nearby plural letters distracts from the singular head box.|The plural "
            "subject letters needs a plural verb.|Two subjects joined by and normally "
            "require a plural verb."
        ),
        (
            "The nearby plural students distracts from the singular head each.|Each is "
            "singular, but of requires the plural group students.|A plural noun inside the "
            "phrase does not make each plural."
        ),
        (
            "The first singular subject is tempting, but the nearer coordinated subject is "
            "plural.|The nearer plural students requires have rather than has.|Neither "
            "pairs with nor, not or."
        ),
    ],
    "gr_tense": [
        (
            "Both events are past, but the earlier event needs the past perfect here.|The "
            "past-perfect event is paired with the wrong present-time reference.|The "
            "progressive auxiliary needs the participle starting, not the base start."
        ),
        (
            "Present residence is true, but since links it to a continuing past "
            "interval.|The progressive describes an ongoing action without the required "
            "perfect time link.|A finished past form loses the continuing-until-now "
            "meaning."
        ),
        (
            "Past progressive describes activity, but does not establish "
            "completion.|Present perfect links to now instead of placing completion before "
            "a past deadline.|Simple present describes routine rather than completion "
            "before that past deadline."
        ),
    ],
    "ew_idea": [
        (
            "A topic identifies a subject, but makes no debatable claim.|A verifiable fact "
            "is useful, but is not the essay's arguable position.|A personal liking cannot "
            "be settled by the evidence the essay evaluates."
        ),
        (
            "Naming schools gives a topic without a position.|A commonplace fact about "
            "classrooms offers little to argue.|Enjoyment expresses taste rather than an "
            "evidence-led claim."
        ),
        (
            "Calling waste a problem is a position, but too broad to direct this "
            "argument.|Promising effects names a subject without claiming which "
            "matter.|Listing opposing preferences avoids taking a defensible position."
        ),
    ],
    "ew_evidence": [
        (
            "Apparent consensus feels persuasive, but supplies no observation.|Saying a "
            "claim is proved repeats it without supporting it.|Disagreement establishes "
            "opinions, not evidence about the claim."
        ),
        (
            "Importance is the claim being defended, not evidence for it.|Consensus "
            "language substitutes agreement for a reason or source.|Condemning stadiums "
            "asserts a judgment without checking it."
        ),
        (
            "Sentence length is visible, but the problem is unsupported reasoning.|Changing "
            "topic does not supply evidence for the original claim.|Repeating a claim "
            "cannot independently support itself."
        ),
    ],
    "ew_structure": [
        (
            "Facts supply material, but need a point and explanation linking "
            "them.|Repetition reinforces wording without developing reasoning.|A story "
            "attracts attention, but must reconnect to the argument."
        ),
        (
            "Repeated judgments sound emphatic, but add no evidence or "
            "explanation.|Relevant facts still need an organising point and a link to the "
            "claim.|An anecdote needs an explicit argumentative connection."
        ),
        (
            "More evidence cannot replace explaining how existing evidence supports the "
            "point.|Expanding the assertion still leaves the reasoning gap.|A quotation is "
            "another source, not automatically the missing explanation."
        ),
    ],
    "bio_cell": [
        (
            "A small grain resembles a unit, but is not alive.|Hair comes from living "
            "cells, but the exposed strand is dead material.|Water is needed for life, but "
            "is not itself a living cell."
        ),
        (
            "A nucleus is a cell structure, but animal cells also have it.|A membrane "
            "surrounds cells of both kinds.|Cytoplasm is present in both plant and animal "
            "cells."
        ),
        (
            "Small size does not remove the need to respond to surroundings.|Being "
            "single-celled does not determine whether an organism is a plant.|Cell number "
            "does not establish whether a nucleus is present."
        ),
    ],
    "bio_transport": [
        (
            "The cell moves, but the question is movement across its membrane.|Chewing "
            "moves food mechanically, not across a cell membrane.|Evaporation loses water "
            "from a surface rather than transporting it into a cell."
        ),
        (
            "Digestion changes food molecules rather than moving water across a "
            "membrane.|Respiration releases energy rather than naming water "
            "movement.|Transpiration concerns plant water loss rather than water entering a "
            "cell."
        ),
        (
            "Ions can diffuse, but not passively against their concentration "
            "gradient.|Water may be nearby, but osmosis names water movement, not ion "
            "uptake.|Passive movement cannot do this, but energy-driven transport can."
        ),
    ],
    "bio_energy": [
        (
            "Water uptake supplies a raw material but does not make sugar.|Respiration uses "
            "sugar to release energy rather than building it with light.|Digestion obtains "
            "food made elsewhere rather than photosynthesising."
        ),
        (
            "Oxygen is familiar from plant gas exchange, but is a product here.|Both gases "
            "occur in the process, but water is an essential input.|Sugar and oxygen are "
            "outputs, not the raw materials for making sugar."
        ),
        (
            "Light supplies energy, but cannot supply the missing carbon atoms.|Water "
            "contains no carbon from which to build sugar.|Removing a required reactant "
            "does not increase the oxygen-producing process."
        ),
    ],
    "bio_systems": [
        (
            "An organ has a job, but a system needs connected specialised jobs.|Physical "
            "proximity does not establish a shared function.|One cell can sustain life, but "
            "is not a system of organs."
        ),
        (
            "The stomach is prominent, but food reaches it through the oesophagus "
            "first.|Absorption is important, but the small intestine follows the "
            "stomach.|The mouth begins the route and the small intestine precedes the large "
            "one."
        ),
        (
            "Body-wide effects suggest connection, but hormones travel through blood rather "
            "than direct attachments.|The liver responds to insulin, but is not the only "
            "tissue affected.|The pancreas secretes hormones; it does not manufacture the "
            "blood."
        ),
    ],
    "chem_particle": [
        (
            "A drop is a small visible unit, but holds many molecules.|A visible salt grain "
            "contains many ions, not a single particle.|An electron is a particle, but only "
            "a constituent of an atom."
        ),
        (
            "Carbon dioxide is a familiar gas, but contains carbon rather than "
            "hydrogen.|Oxygen is part of water, but oxygen gas has no hydrogen.|Salt is a "
            "familiar compound, but contains neither hydrogen nor oxygen."
        ),
        (
            "An electron is negative, but losing it leaves a positive imbalance.|The lost "
            "electron changes charge even though it changes little mass.|Element identity "
            "depends on protons, not the electron count."
        ),
    ],
    "chem_change": [
        (
            "A visible state change can look dramatic, but the substance remains "
            "water.|Dissolving hides the salt without necessarily changing its chemical "
            "identity.|Breaking changes size and shape, not chemical composition."
        ),
        (
            "Boiling changes state while the substance remains water.|Dissolving disperses "
            "sugar without the new substances made by burning.|Bending changes shape rather "
            "than making a new substance."
        ),
        (
            "Heating can accompany reactions but is not required for one.|A similar liquid "
            "volume does not show unchanged chemical composition.|Dilution can fade colour, "
            "but does not explain a newly formed solid."
        ),
    ],
    "chem_equation": [
        (
            "A word equation names substances but does not supply balanced formulae.|The "
            "shared word equation hides the difference between algebra and atom "
            "conservation.|An element list omits reactants, products and their proportions."
        ),
        (
            "Matching formula units is not enough; the oxygen atom counts differ.|Equal "
            "coefficients still leave different oxygen counts on each side.|Increasing only "
            "oxygen worsens the imbalance rather than conserving atoms."
        ),
        (
            "Changing the formula would change the substance rather than balance the "
            "reaction.|Diatomic reactants still require equal atom totals in the "
            "products.|An arbitrary coefficient does not balance both hydrogen and oxygen."
        ),
    ],
    "wh_evidence": [
        (
            "A novel evokes the past but was not made by that community at that time.|A "
            "reconstruction visualises evidence but is a later interpretation.|A present "
            "belief needs surviving evidence rather than replacing it."
        ),
        (
            "A textbook may be reliable, but is a later secondary account.|A documentary "
            "can cite evidence but is not itself a contemporary record here.|Historical "
            "fiction depicts a period without being contemporary testimony."
        ),
        (
            "Missing writing restricts some questions, but material evidence still "
            "survives.|Objects provide evidence without containing the assertions of a "
            "written document.|Later accounts cannot justify inventing the content of "
            "missing records."
        ),
    ],
    "wh_sources": [
        (
            "Stated facts matter, but purpose and audience affect how to read them.|Age "
            "establishes timing, not neutrality.|A recruitment message reveals persuasion, "
            "not necessarily lived experience."
        ),
        (
            "Fee levels matter to the debate, but do not establish the editorial's "
            "interest.|Length describes format rather than motive or audience.|Paper "
            "quality does not explain the political interest in the argument."
        ),
        (
            "Bias warrants scrutiny, not automatic rejection of every report.|Brevity does "
            "not decide truthfulness.|Rejecting a sweeping claim with another unsupported "
            "universal claim supplies no evidence."
        ),
    ],
    "wh_causes": [
        (
            "Earlier events supply chronology, but chronology alone does not rank causes.|A "
            "broad claim about human nature does not explain this particular war.|Later "
            "consequences do not establish what brought the war about."
        ),
        (
            "A dramatic incident can trigger events without being an underlying "
            "condition.|A declaration date marks an event rather than a long-running "
            "pressure.|Same-day weather is immediate context, not the structural cause "
            "given here."
        ),
        (
            "Disagreement about causes can concern interpretation despite agreed "
            "facts.|Multiple interpretations do not mean events were causeless.|Debatable "
            "weighting calls for argument, not abandoning causal discussion."
        ),
    ],
    "sw_neno": [
        (
            "Neno linataja kitu kinachofanywa, lakini tendo si nomino.|Maelezo yanahusu "
            "kitu, lakini kivumishi si jina la kitu.|Jinsi ya kutenda inahusiana na tendo, "
            "lakini si nomino."
        ),
        (
            "Soma ni neno la kawaida, lakini linaeleza tendo, si jina.|Nzuri linaeleza "
            "sifa, si jina la mtu au kitu.|Haraka linaeleza jinsi tendo linafanyika, si "
            "jina."
        ),
        (
            "Wanafunzi ni wanaoelezewa, lakini si sifa yenyewe.|Wanasoma linaeleza tendo "
            "badala ya sifa ya wanafunzi.|Bidii ni nomino katika kirai, si kivumishi "
            "kinachoeleza wanafunzi."
        ),
    ],
    "sw_ngeli": [
        (
            "Hii na mpya vinafahamika, lakini havikubaliani na ngeli ya kitabu.|Vitabu ni "
            "wingi, lakini hiki na kipya ni umoja.|New linaeleza sifa kwa Kiingereza bila "
            "upatanisho wa Kiswahili."
        ),
        (
            "Hii na mpya ni umoja wa ngeli nyingine, si viambishi vya vitabu.|Ule "
            "linaonyesha kitu, lakini halikubaliani na vitabu.|Hilo na kipya vinachanganya "
            "ngeli na umoja badala ya wingi wa vi-."
        ),
        (
            "Walimu ni watu, lakini kiunganishi hufuata ngeli ya vitabu.|Cha hufuata umoja "
            "wa ki-, lakini vitabu ni wingi wa vi-.|Ya hutumika katika ngeli nyingine, si "
            "upatanisho wa vitabu."
        ),
    ],
    "sw_vitenzi": [
        (
            "Soma linaonyesha tendo, lakini halitaji nafsi wala wakati.|Kitabu ni neno "
            "lenye maana, lakini ni nomino, si kitenzi.|Haraka linaeleza jinsi, si nafsi na "
            "wakati ndani ya kitenzi."
        ),
        (
            "Wa- linaonyesha nafsi ya wingi, si wakati ujao.|Mzizi unaonyesha tendo la "
            "kusoma, si wakati.|Kishia kinafunga kitenzi, lakini hakionyeshi wakati ujao."
        ),
        (
            "Nafsi na wakati vipo, lakini kiambishi cha yambwa kitabu kinakosekana.|Yambwa "
            "lipo, lakini a- ni nafsi ya umoja badala ya wao.|Viambishi vingi vipo, lakini "
            "kishia -e hubadili hali ya kitenzi."
        ),
    ],
}


def authored_distractors(concept) -> list[dict[str, str]]:
    rows = REASONS[concept.id]
    result = []
    for rung, reasons in zip((1, 3, 4), rows, strict=True):
        item = concept.kit[rung]
        distractors = [
            text for index, text in enumerate(item["options"]) if index != item["answer"]
        ]
        for index, (wrong, reason) in enumerate(zip(distractors, reasons.split("|"), strict=True)):
            result.append(
                {
                    "id": f"mis_{concept.id}_r{rung}_{index}",
                    "wrong_belief": wrong,
                    "why_tempting": reason,
                    "correction": item["why"],
                    "rung": rung,
                }
            )
    return result
