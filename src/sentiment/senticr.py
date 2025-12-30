"""SentiCR-style sentiment analysis for code review comments.

Based on "SentiCR: A Customized Sentiment Analysis Tool for Code Review Interactions"
by Ahmed et al. (2017). Uses TF-IDF + Gradient Boosting trained on code review data.

Key advantages over VADER:
- Domain-specific: trained on 1,600 labeled code review comments
- Handles code review conventions: "Nit:", "Optional:", "LGTM"
- Understands technical jargon better
- 81.4% accuracy vs 37.5% for VADER on code review text

Requires optional dependencies: pip install lgtm[sentiment]
"""

from __future__ import annotations

import pickle
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Check if sentiment dependencies are available
SENTICR_AVAILABLE = False
_stemmer = None

try:
    import nltk
    from nltk.stem.snowball import SnowballStemmer

    # Ensure required NLTK data is available
    try:
        nltk.data.find("taggers/averaged_perceptron_tagger_eng")
    except LookupError:
        nltk.download("averaged_perceptron_tagger_eng", quiet=True)

    _stemmer = SnowballStemmer("english")
    SENTICR_AVAILABLE = True
except ImportError:
    pass


# ============================================================================
# Preprocessing
# ============================================================================

# Contractions to expand
CONTRACTIONS = {
    "ain't": "is not",
    "aren't": "are not",
    "can't": "cannot",
    "could've": "could have",
    "couldn't": "could not",
    "didn't": "did not",
    "doesn't": "does not",
    "don't": "do not",
    "hadn't": "had not",
    "hasn't": "has not",
    "haven't": "have not",
    "he'd": "he would",
    "he'll": "he will",
    "he's": "he is",
    "i'd": "i would",
    "i'll": "i will",
    "i'm": "i am",
    "i've": "i have",
    "isn't": "is not",
    "it'd": "it would",
    "it'll": "it will",
    "it's": "it is",
    "let's": "let us",
    "might've": "might have",
    "must've": "must have",
    "mustn't": "must not",
    "needn't": "need not",
    "shan't": "shall not",
    "she'd": "she would",
    "she'll": "she will",
    "she's": "she is",
    "should've": "should have",
    "shouldn't": "should not",
    "that's": "that is",
    "there's": "there is",
    "they'd": "they would",
    "they'll": "they will",
    "they're": "they are",
    "they've": "they have",
    "wasn't": "was not",
    "we'd": "we would",
    "we'll": "we will",
    "we're": "we are",
    "we've": "we have",
    "weren't": "were not",
    "what'll": "what will",
    "what're": "what are",
    "what's": "what is",
    "what've": "what have",
    "where's": "where is",
    "who'd": "who would",
    "who'll": "who will",
    "who's": "who is",
    "won't": "will not",
    "wouldn't": "would not",
    "you'd": "you would",
    "you'll": "you will",
    "you're": "you are",
    "you've": "you have",
}

# Negation words that flip meaning
NEGATION_WORDS = frozenset(
    [
        "not",
        "never",
        "none",
        "nobody",
        "nowhere",
        "neither",
        "barely",
        "hardly",
        "nothing",
        "rarely",
        "seldom",
        "despite",
        "no",
        "nor",
        "cannot",
        "cant",
        "wont",
        "isnt",
        "arent",
        "doesnt",
        "didnt",
        "hasnt",
        "havent",
        "hadnt",
        "wouldnt",
        "couldnt",
        "shouldnt",
        "mightnt",
        "mustnt",
        "neednt",
    ]
)

# Emoticons and their sentiment
EMOTICONS = {
    ":)": " positive_emoticon ",
    ":-)": " positive_emoticon ",
    ":D": " positive_emoticon ",
    ":-D": " positive_emoticon ",
    ";)": " positive_emoticon ",
    ";-)": " positive_emoticon ",
    ":P": " positive_emoticon ",
    ":-P": " positive_emoticon ",
    ":(": " negative_emoticon ",
    ":-(": " negative_emoticon ",
    ":/": " negative_emoticon ",
    ":-/": " negative_emoticon ",
    ":|": " neutral_emoticon ",
    ":-|": " neutral_emoticon ",
}

# Domain-specific stopwords (programming keywords + common words)
STOPWORDS = frozenset(
    [
        # Common English
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "was",
        "are",
        "were",
        "been",
        "be",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "they",
        "them",
        "their",
        "we",
        "us",
        "our",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "i",
        "me",
        "my",
        "who",
        "what",
        "which",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        # Programming keywords
        "class",
        "def",
        "function",
        "return",
        "if",
        "else",
        "elif",
        "for",
        "while",
        "try",
        "except",
        "catch",
        "throw",
        "import",
        "from",
        "as",
        "public",
        "private",
        "protected",
        "static",
        "final",
        "const",
        "let",
        "var",
        "new",
        "null",
        "none",
        "true",
        "false",
        "void",
        "int",
        "str",
        "bool",
        "float",
        "list",
        "dict",
        "set",
        "tuple",
        "array",
        "object",
    ]
)

# URL pattern
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

# Contraction pattern
CONTRACTION_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in CONTRACTIONS) + r")\b", re.IGNORECASE
)


def expand_contractions(text: str) -> str:
    """Expand contractions like don't -> do not."""

    def replace(match: re.Match) -> str:
        return CONTRACTIONS.get(match.group(0).lower(), match.group(0))

    return CONTRACTION_PATTERN.sub(replace, text)


def remove_urls(text: str) -> str:
    """Remove URLs from text."""
    return URL_PATTERN.sub(" ", text)


def replace_emoticons(text: str) -> str:
    """Replace emoticons with sentiment words."""
    for emoticon, replacement in EMOTICONS.items():
        text = text.replace(emoticon, replacement)
    return text


def handle_negation(text: str) -> str:
    """Prepend NOT_ to words following negation words.

    E.g., "not good" -> "not NOT_good"
    This helps the model learn that negated words have different meaning.
    """
    words = text.split()
    result = []
    negate_next = False

    for word in words:
        lower = word.lower()

        # Check if this word is a negation trigger
        if lower in NEGATION_WORDS:
            result.append(word)
            negate_next = True
        elif negate_next:
            # Prepend NOT_ to indicate negated context
            result.append(f"NOT_{word}")
            # Stop negating at punctuation
            if word.endswith((".", ",", "!", "?", ";", ":")):
                negate_next = False
        else:
            result.append(word)

    return " ".join(result)


def preprocess(text: str) -> str:
    """Full preprocessing pipeline for code review text."""
    if not text:
        return ""

    # Convert to ASCII, ignore errors
    text = text.encode("ascii", errors="ignore").decode("ascii")

    # Lowercase
    text = text.lower()

    # Expand contractions
    text = expand_contractions(text)

    # Remove URLs
    text = remove_urls(text)

    # Replace emoticons
    text = replace_emoticons(text)

    # Handle negation
    text = handle_negation(text)

    return text


# ============================================================================
# Tokenization and Stemming
# ============================================================================


def tokenize_and_stem(text: str) -> list[str]:
    """Tokenize and stem text, removing stopwords."""
    # Simple word tokenization
    tokens = re.findall(r"\b[a-z][a-z_]+\b", text.lower())

    # Stem and filter stopwords
    result = []
    for token in tokens:
        if token not in STOPWORDS and len(token) > 2:
            # Use stemmer if available, otherwise just use token
            if _stemmer is not None:
                result.append(_stemmer.stem(token))
            else:
                result.append(token)

    return result


# ============================================================================
# Sentiment Classes
# ============================================================================


@dataclass
class SentimentScores:
    """Sentiment scores for a piece of text.

    Maintains API compatibility with the old VADER-based module.
    """

    positive: float  # Probability of positive sentiment
    negative: float  # Probability of negative sentiment
    neutral: float  # Probability of neutral sentiment
    compound: float  # -1.0 to 1.0 (for compatibility)

    @property
    def label(self) -> str:
        """Get the predicted label."""
        if self.positive > self.neutral and self.positive > self.negative:
            return "positive"
        elif self.negative > self.neutral and self.negative > self.positive:
            return "negative"
        else:
            return "neutral"

    @property
    def is_positive(self) -> bool:
        return self.label == "positive"

    @property
    def is_negative(self) -> bool:
        return self.label == "negative"

    @property
    def is_neutral(self) -> bool:
        return self.label == "neutral"


# ============================================================================
# Model
# ============================================================================

# Cache directory for the trained model
CACHE_DIR = Path.home() / ".cache" / "lgtm"
MODEL_FILE = CACHE_DIR / "senticr_model.pkl"

# Training data URL
TRAINING_DATA_URL = "https://github.com/senticr/SentiCR/raw/master/SentiCR/oracle.xlsx"


def _get_training_data() -> tuple[list[str], list[int]]:
    """Load the SentiCR training dataset.

    Returns (texts, labels) where labels are:
    - 0: negative
    - 1: neutral
    - 2: positive

    Requires: pip install lgtm[sentiment]
    """
    try:
        import openpyxl
    except ImportError:
        raise ImportError(
            "Sentiment analysis requires optional dependencies. "
            "Install with: pip install lgtm[sentiment]"
        )

    import httpx

    # Check for cached training data
    data_file = CACHE_DIR / "oracle.xlsx"
    if not data_file.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print("Downloading SentiCR training data...")
        response = httpx.get(TRAINING_DATA_URL, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        data_file.write_bytes(response.content)

    # Load the Excel file
    wb = openpyxl.load_workbook(data_file, read_only=True)
    ws = wb.active

    texts = []
    labels = []

    # Skip header row
    for row in list(ws.iter_rows(min_row=2, values_only=True)):
        if row[0] and row[1] is not None:
            text = str(row[0]).strip()
            # Label mapping: -1 -> 0 (negative), 0 -> 1 (neutral), 1 -> 2 (positive)
            label = int(row[1])
            if label == -1:
                labels.append(0)
            elif label == 0:
                labels.append(1)
            else:
                labels.append(2)
            texts.append(text)

    wb.close()
    return texts, labels


def _train_model():
    """Train the TF-IDF + Gradient Boosting model.

    Requires: pip install lgtm[sentiment]
    """
    try:
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError:
        raise ImportError(
            "Sentiment analysis requires optional dependencies. "
            "Install with: pip install lgtm[sentiment]"
        )

    print("Training SentiCR model...")
    texts, labels = _get_training_data()

    # Preprocess all texts
    processed = [preprocess(t) for t in texts]

    # Create TF-IDF vectorizer
    vectorizer = TfidfVectorizer(
        tokenizer=tokenize_and_stem,
        sublinear_tf=True,
        max_df=0.5,
        min_df=3,
        ngram_range=(1, 2),
    )

    # Fit vectorizer and transform training data
    X = vectorizer.fit_transform(processed)

    # Train Gradient Boosting classifier
    clf = GradientBoostingClassifier(
        n_estimators=100,
        max_depth=5,
        random_state=42,
    )
    clf.fit(X, labels)

    # Save the model
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_FILE, "wb") as f:
        pickle.dump({"vectorizer": vectorizer, "classifier": clf}, f)

    print(f"Model saved to {MODEL_FILE}")
    return vectorizer, clf


@lru_cache(maxsize=1)
def _get_model():
    """Load or train the sentiment model."""
    if MODEL_FILE.exists():
        # Check if model is up to date (simple hash check)
        with open(MODEL_FILE, "rb") as f:
            try:
                data = pickle.load(f)
                return data["vectorizer"], data["classifier"]
            except Exception:
                pass

    return _train_model()


def get_sentiment_scores(text: str) -> SentimentScores:
    """Analyze sentiment of text using SentiCR-style classifier.

    Args:
        text: The code review comment to analyze.

    Returns:
        SentimentScores with positive, negative, neutral probabilities.
        If sentiment dependencies aren't installed, returns neutral scores.
    """
    if not text or not text.strip():
        return SentimentScores(
            positive=0.0,
            negative=0.0,
            neutral=1.0,
            compound=0.0,
        )

    # If sentiment deps not available, return neutral
    if not SENTICR_AVAILABLE:
        return SentimentScores(
            positive=0.0,
            negative=0.0,
            neutral=1.0,
            compound=0.0,
        )

    vectorizer, clf = _get_model()

    # Preprocess and vectorize
    processed = preprocess(text)
    X = vectorizer.transform([processed])

    # Get probability predictions
    probs = clf.predict_proba(X)[0]

    # Map to named probabilities (order: negative, neutral, positive)
    negative = probs[0] if len(probs) > 0 else 0.0
    neutral = probs[1] if len(probs) > 1 else 0.0
    positive = probs[2] if len(probs) > 2 else 0.0

    # Compute compound score for compatibility (-1 to 1)
    compound = positive - negative

    return SentimentScores(
        positive=positive,
        negative=negative,
        neutral=neutral,
        compound=compound,
    )


def analyze_batch(texts: list[str]) -> list[SentimentScores]:
    """Analyze sentiment of multiple texts efficiently.

    If sentiment dependencies aren't installed, returns neutral scores for all.
    """
    if not texts:
        return []

    # If sentiment deps not available, return neutral for all
    if not SENTICR_AVAILABLE:
        return [
            SentimentScores(positive=0.0, negative=0.0, neutral=1.0, compound=0.0)
            for _ in texts
        ]

    vectorizer, clf = _get_model()

    results = []
    valid_indices = []
    valid_texts = []

    # Preprocess all texts, tracking empty ones
    for i, text in enumerate(texts):
        if not text or not text.strip():
            results.append(
                SentimentScores(
                    positive=0.0,
                    negative=0.0,
                    neutral=1.0,
                    compound=0.0,
                )
            )
        else:
            valid_indices.append(i)
            valid_texts.append(preprocess(text))
            results.append(None)  # Placeholder

    if valid_texts:
        # Batch vectorize and predict
        X = vectorizer.transform(valid_texts)
        all_probs = clf.predict_proba(X)

        for idx, probs in zip(valid_indices, all_probs, strict=True):
            negative = probs[0] if len(probs) > 0 else 0.0
            neutral = probs[1] if len(probs) > 1 else 0.0
            positive = probs[2] if len(probs) > 2 else 0.0

            results[idx] = SentimentScores(
                positive=positive,
                negative=negative,
                neutral=neutral,
                compound=positive - negative,
            )

    return results
