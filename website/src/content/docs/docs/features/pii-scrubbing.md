---
title: PII scrubbing
description: Automatic detection and redaction of sensitive entities using Microsoft Presidio and spaCy NER.
---

PII scrubbing ensures sensitive data is never sent to an LLM provider. Entities are detected in the prompt, replaced with deterministic placeholders, and then restored in the response before it reaches the client.

## How it works

```
User prompt:    "My name is Alice Smith, email alice@example.com"
                          ↓  stage 05: scrub
To LLM:         "My name is <<PII_PERSON_a3f8>>, email <<PII_EMAIL_ADDRESS_b1c2>>"
                          ↓  LLM responds
From LLM:       "Hello <<PII_PERSON_a3f8>>! I'll contact you at <<PII_EMAIL_ADDRESS_b1c2>>"
                          ↓  stage 09: restore
Client gets:    "Hello Alice Smith! I'll contact you at alice@example.com"
```

The placeholder `<<PII_ENTITY_TYPE_hash>>` is:
- **Deterministic** — same input value always produces the same placeholder within a request
- **Reversible** — the mapping is stored in request context and used to restore values in the response
- **Opaque** — the hash is a truncated SHA-256 of the original value; the original cannot be derived from the placeholder

## Detected entities

Configured in `config.yaml` under `pii.entities`:

| Entity | Examples |
|---|---|
| `PERSON` | Alice Smith, Dr. Johnson |
| `EMAIL_ADDRESS` | alice@example.com |
| `PHONE_NUMBER` | +1-555-867-5309 |
| `CREDIT_CARD` | 4111 1111 1111 1111 |
| `US_SSN` | 123-45-6789 |
| `IP_ADDRESS` | 192.168.1.1 |
| `LOCATION` | 221B Baker Street, London |

Add or remove entity types in `config.yaml`:

```yaml
pii:
  entities:
    - PERSON
    - EMAIL_ADDRESS
    - PHONE_NUMBER
    - CREDIT_CARD
    - US_SSN
    - IP_ADDRESS
    - LOCATION
```

## Allow list

Terms in `pii.allow_list` are never scrubbed, regardless of Presidio's confidence. Useful for internal class names, product names, or other identifiers that the NER model consistently mis-classifies.

```yaml
pii:
  allow_list:
    - Settings    # class name detected as a person name
    - Config
    - Manager
```

Matching is case-insensitive — `"settings"` in the allow list protects `Settings`, `SETTINGS`, etc.

## Score threshold

`pii.score_threshold` (default `0.7`) controls Presidio's minimum confidence before an entity is redacted. Lower values catch more entities but increase false positives.

```yaml
pii:
  score_threshold: 0.7
```

## Code and diff handling

Messages that are git diffs (containing `diff --git` or a unified hunk header `@@ -N,N +N,N @@`) are passed through **without scrubbing**. Variable names, class names, and other identifiers in code produce too many false positives to make scrubbing useful in this context.

Regular code blocks inside ` ``` ` fences are still scrubbed — a docstring or comment inside a code block can contain real emails or phone numbers.

## Engine

Detection uses **Microsoft Presidio** with a spaCy `en_core_web_lg` NER backend. The spaCy model is loaded on startup (it's ~800 MB — this is why probes have a 60-second initial delay).

## Disabling

```yaml
pii:
  enabled: false
```

The scrubber still initialises (keeping startup time the same), but all requests pass through unchanged.

## Limitations

- English language only (spaCy `en_core_web_lg`)
- Does not scrub binary data or file uploads
- Cannot restore PII if the LLM paraphrases the placeholder (e.g. "the person mentioned earlier") rather than echoing it verbatim
- Context-dependent entities (e.g. a company name that is also a common word) may be missed at threshold 0.7
