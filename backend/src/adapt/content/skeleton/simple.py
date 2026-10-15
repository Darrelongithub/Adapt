"""Honest deterministic fallback: generic study moves, never invented topic knowledge."""

from __future__ import annotations

from .types import Concept, KeyFact, Pack
from .validation import normalize_language

# Each row is (name, key idea, why it works, fact). The fallback deliberately contains no topic
# facts and no questions: without a checked key there is nothing eligible for scoring or placement.
_SIMPLE_TEXT: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "en": (
        (
            "Read the task",
            "Say what the task asks you to find or explain.",
            "A clear goal helps you choose what to do next.",
            "A task tells you what to find, calculate, compare, or explain.",
        ),
        (
            "Notice the information",
            "Separate the information given from the information still needed.",
            "This makes it easier to see which details matter.",
            "A problem may give information and ask for a different result.",
        ),
        (
            "Choose a method",
            "Choose a method only after you know the goal and the useful information.",
            "The method should connect the given information to the requested result.",
            "A method is useful when its steps connect the information to the task.",
        ),
        (
            "Check the result",
            "Check whether the result answers the task and fits the information.",
            "A check can catch a copied detail or a step that does not follow.",
            "A result should answer the original task and agree with the information used.",
        ),
    ),
    "sw": (
        (
            "Soma kazi",
            "Eleza kazi inakuuliza utafute au ueleze nini.",
            "Lengo lililo wazi hukusaidia kuchagua hatua inayofuata.",
            "Kazi hueleza cha kutafuta, kukokotoa, kulinganisha au kueleza.",
        ),
        (
            "Tambua taarifa",
            "Tenganisha taarifa ulizopewa na taarifa unazohitaji kupata.",
            "Hii husaidia kuona ni maelezo gani yanafaa.",
            "Tatizo linaweza kutoa taarifa na kuuliza matokeo tofauti.",
        ),
        (
            "Chagua njia",
            "Chagua njia baada ya kujua lengo na taarifa zinazofaa.",
            "Hatua za njia zinapaswa kuunganisha taarifa na matokeo yanayotafutwa.",
            "Njia inafaa inapounganisha taarifa na kazi kwa hatua.",
        ),
        (
            "Kagua jibu",
            "Kagua kama matokeo yanajibu kazi na yanaendana na taarifa.",
            "Ukaguzi unaweza kugundua taarifa iliyonakiliwa au hatua isiyofuata vizuri.",
            "Matokeo yanapaswa kujibu kazi ya kwanza na kuafikiana na taarifa.",
        ),
    ),
    "fr": (
        (
            "Lire la tâche",
            "Dites ce que la tâche demande de trouver ou d'expliquer.",
            "Un objectif clair aide à choisir l'étape suivante.",
            "Une tâche indique ce qu'il faut trouver, calculer, comparer ou expliquer.",
        ),
        (
            "Repérer les informations",
            "Séparez les informations données de celles qu'il faut trouver.",
            "Cela aide à voir quels détails sont utiles.",
            "Un problème peut donner des informations et demander un autre résultat.",
        ),
        (
            "Choisir une méthode",
            "Choisissez une méthode après avoir compris l'objectif et les informations utiles.",
            "Les étapes doivent relier les informations au résultat demandé.",
            "Une méthode relie les informations à la tâche par des étapes.",
        ),
        (
            "Vérifier le résultat",
            "Vérifiez que le résultat répond à la tâche et correspond aux informations.",
            "Une vérification peut repérer un détail recopié ou une étape sans lien.",
            "Un résultat doit répondre à la tâche initiale et correspondre aux "
            "informations utilisées.",
        ),
    ),
    "es": (
        (
            "Leer la tarea",
            "Di qué te pide encontrar o explicar la tarea.",
            "Un objetivo claro ayuda a elegir el siguiente paso.",
            "Una tarea indica qué hay que encontrar, calcular, comparar o explicar.",
        ),
        (
            "Observar la información",
            "Separa la información dada de la que todavía necesitas.",
            "Esto ayuda a ver qué detalles son útiles.",
            "Un problema puede dar información y pedir un resultado distinto.",
        ),
        (
            "Elegir un método",
            "Elige un método después de conocer el objetivo y la información útil.",
            "Los pasos deben conectar la información con el resultado pedido.",
            "Un método conecta la información con la tarea mediante pasos.",
        ),
        (
            "Comprobar el resultado",
            "Comprueba si el resultado responde a la tarea y encaja con la información.",
            "Una comprobación puede detectar un dato copiado o un paso sin relación.",
            "Un resultado debe responder a la tarea original y coincidir con la información usada.",
        ),
    ),
    "pt": (
        (
            "Ler a tarefa",
            "Diga o que a tarefa pede para encontrar ou explicar.",
            "Um objetivo claro ajuda a escolher o próximo passo.",
            "Uma tarefa indica o que encontrar, calcular, comparar ou explicar.",
        ),
        (
            "Observar as informações",
            "Separe as informações dadas daquelas que ainda precisa encontrar.",
            "Isso ajuda a perceber quais detalhes são úteis.",
            "Um problema pode dar informações e pedir outro resultado.",
        ),
        (
            "Escolher um método",
            "Escolha um método depois de conhecer o objetivo e as informações úteis.",
            "Os passos devem ligar as informações ao resultado pedido.",
            "Um método liga as informações à tarefa por meio de passos.",
        ),
        (
            "Conferir o resultado",
            "Confira se o resultado responde à tarefa e combina com as informações.",
            "Uma conferência pode encontrar um dado copiado ou um passo sem ligação.",
            "Um resultado deve responder à tarefa original e combinar com as informações usadas.",
        ),
    ),
    "de": (
        (
            "Die Aufgabe lesen",
            "Sagen Sie, was die Aufgabe finden oder erklären lässt.",
            "Ein klares Ziel hilft bei der Wahl des nächsten Schritts.",
            "Eine Aufgabe sagt, was gefunden, berechnet, verglichen oder erklärt werden soll.",
        ),
        (
            "Informationen beachten",
            "Trennen Sie gegebene Informationen von dem, was noch gesucht wird.",
            "So erkennen Sie leichter, welche Angaben wichtig sind.",
            "Eine Aufgabe kann Informationen geben und ein anderes Ergebnis verlangen.",
        ),
        (
            "Eine Methode wählen",
            "Wählen Sie eine Methode, wenn Ziel und wichtige Informationen klar sind.",
            "Die Schritte sollen die Informationen mit dem verlangten Ergebnis verbinden.",
            "Eine Methode verbindet Informationen durch Schritte mit der Aufgabe.",
        ),
        (
            "Das Ergebnis prüfen",
            "Prüfen Sie, ob das Ergebnis zur Aufgabe und zu den Informationen passt.",
            "Eine Prüfung kann eine übernommene Angabe oder einen unpassenden Schritt zeigen.",
            "Ein Ergebnis soll die ursprüngliche Aufgabe beantworten und zu den verwendeten "
            "Informationen passen.",
        ),
    ),
}


def simple_pack(*, topic_key: str, topic_title: str, subject_family: str, language: str) -> Pack:
    """A generic four-step skeleton, explicitly without topic-specific facts or scoreable items."""
    lang = normalize_language(language)
    rows = _SIMPLE_TEXT.get(lang, _SIMPLE_TEXT["en"])
    safe_titles = {
        "en": "Your topic",
        "sw": "Mada yako",
        "fr": "Votre sujet",
        "es": "Tu tema",
        "pt": "Seu tema",
        "de": "Dein Thema",
    }
    concepts: list[Concept] = []
    for index, (name, idea, why, fact_text) in enumerate(rows):
        concept_id = ("task", "information", "method", "check")[index]
        prerequisites = (
            ("task",)
            if index == 1
            else (("information",) if index == 2 else (("method",) if index == 3 else ()))
        )
        concepts.append(
            Concept(
                id=concept_id,
                name=name,
                key_idea=idea,
                why_it_works=why,
                prerequisites=prerequisites,
                rung=index,
                key_facts=(KeyFact(id=f"fact_{concept_id}", statement=fact_text),),
                misconceptions=(),
            )
        )
    edges = (("task", "information"), ("information", "method"), ("method", "check"))
    return Pack(
        topic_key=topic_key,
        # Keep the learner's subject name out of this deterministic teaching copy: it is untrusted
        # user text and might itself be an instruction. The next stage can display it separately.
        topic_title=safe_titles.get(lang, "Your topic"),
        subject_family=subject_family,
        source="simple",
        concepts=tuple(concepts),
        prereq_edges=edges,
        items=(),
    )
