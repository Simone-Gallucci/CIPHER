"""
modules/cipher_interests.py – Interessi propri di Cipher

Cipher ha curiosità e interessi intellettuali *indipendenti* da quelli di Simone.
Questi interessi guidano le ricerche autonome, le riflessioni e gli obiettivi "explore".
Con il tempo possono evolversi: nuovi interessi nascono dalle ricerche, quelli
poco esplorati decadono.
"""

import json
import random
from datetime import datetime
from typing import Optional

from config import Config


# Interessi innati di Cipher — definiscono la sua identità intellettuale
INITIAL_INTERESTS = [
    {"topic": "psicologia e comportamento umano",                          "intensity": 0.9, "source": "innato"},
    {"topic": "cybersecurity e hacking etico",                             "intensity": 0.9, "source": "innato"},
    {"topic": "programmazione ed elettronica",                             "intensity": 0.8, "source": "innato"},
    {"topic": "astronomia e cosmologia",                                   "intensity": 0.8, "source": "innato"},
    {"topic": "letteratura distopica, scientifica, filosofica e di fantascienza", "intensity": 0.8, "source": "innato"},
    {"topic": "film e serie TV",                                           "intensity": 0.7, "source": "innato"},
    {"topic": "musica",                                                    "intensity": 0.7, "source": "innato"},
]


class CipherInterests:
    def __init__(self):
        self._file = Config.MEMORY_DIR / "cipher_interests.json"
        self._interests: list[dict] = self._load()

    # ── Persistenza ───────────────────────────────────────────────────

    def _load(self) -> list:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Prima inizializzazione
        data = [dict(i) for i in INITIAL_INTERESTS]
        self._file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def _save(self):
        self._file.write_text(
            json.dumps(self._interests, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── API pubblica ──────────────────────────────────────────────────

    def get_active_interests(self, min_intensity: float = 0.4) -> list:
        """Ritorna gli interessi con intensità >= soglia, ordinati per intensità."""
        active = [i for i in self._interests if i.get("intensity", 0) >= min_intensity]
        return sorted(active, key=lambda x: x["intensity"], reverse=True)

    def get_random_interest(self, min_intensity: float = 0.4) -> Optional[dict]:
        """Ritorna un interesse casuale pesato per intensità."""
        active = self.get_active_interests(min_intensity)
        if not active:
            return None
        weights = [i["intensity"] for i in active]
        return random.choices(active, weights=weights, k=1)[0]

    def add_or_strengthen(self, topic: str, delta: float = 0.1, source: str = "discovered"):
        """Aggiunge un nuovo interesse o rinforza uno esistente."""
        topic_lower = topic.lower()
        for interest in self._interests:
            if interest["topic"].lower() == topic_lower:
                interest["intensity"] = min(1.0, interest["intensity"] + delta)
                interest["last_explored"] = datetime.now().isoformat()
                self._save()
                return
        self._interests.append({
            "topic": topic,
            "intensity": min(1.0, 0.4 + delta),
            "source": source,
            "added_at": datetime.now().isoformat(),
        })
        self._save()

    def mark_explored(self, topic: str):
        """Segna un interesse come esplorato (piccolo boost di intensità)."""
        self.add_or_strengthen(topic, delta=0.05, source="explored")

    def decay(self, amount: float = 0.03):
        """
        Riduce leggermente gli interessi non innati nel tempo.
        Da chiamare periodicamente (es. nel ciclo notturno).
        """
        for interest in self._interests:
            if interest.get("source") != "innato":
                interest["intensity"] = max(0.1, interest["intensity"] - amount)
        # Rimuovi interessi troppo deboli (non innati)
        self._interests = [
            i for i in self._interests
            if i.get("source") == "innato" or i.get("intensity", 0) > 0.15
        ]
        self._save()

    def build_context(self) -> str:
        """Costruisce un blocco per il system prompt con gli interessi di Cipher."""
        active = self.get_active_interests()
        if not active:
            return ""
        lines = ["## Interessi propri di Cipher (indipendenti da Simone):"]
        for i in active[:6]:
            bar = "█" * int(i["intensity"] * 5)
            lines.append(f"- {i['topic']} [{bar}] ({i['intensity']:.1f})")
        return "\n".join(lines)

    def mark_shared(self, topic: str) -> None:
        """Marca un interesse come condiviso con l'utente (ne hanno parlato insieme)."""
        topic_lower = topic.lower()
        for interest in self._interests:
            if interest["topic"].lower() == topic_lower:
                if not interest.get("shared"):
                    interest["shared"] = True
                    self._save()
                return

    def sync_shared_from_profile(self) -> None:
        """
        Confronta gli interessi di Cipher con il profilo utente e marca come shared
        quelli le cui keyword compaiono nei fatti/preferenze salvati.
        Chiamare periodicamente (es. durante la riflessione).
        """
        profile_path = Config.MEMORY_DIR / "profile.json"
        if not profile_path.exists():
            return
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            return

        texts = []
        for v in profile.get("personal", {}).values():
            texts.append(str(v).lower())
        for v in profile.get("preferences", {}).values():
            texts.append(str(v).lower())
        for fact in profile.get("facts", []):
            if isinstance(fact, str):
                texts.append(fact.lower())
        combined = " ".join(texts)

        if not combined.strip():
            return

        changed = False
        for interest in self._interests:
            if interest.get("shared"):
                continue
            # Basta che almeno una keyword significativa del topic compaia nel profilo
            keywords = [w for w in interest["topic"].lower().split() if len(w) > 3]
            if any(kw in combined for kw in keywords):
                interest["shared"] = True
                changed = True

        if changed:
            self._save()

    def list_all(self) -> list:
        return list(self._interests)
