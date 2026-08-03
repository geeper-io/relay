---
title: PII scrubbing
description: Automatic detection and redaction of sensitive entities using Microsoft Presidio and spaCy NER.
---

PII scrubbing ensures sensitive data is never sent to an LLM provider. Entities are detected in the prompt, replaced with deterministic placeholders, and then restored in the response before it reaches the client.

## How it works

```
User prompt:    "My name is Alice Smith, email alice@example.com"
                          ↓  stage 05: scrub
To LLM:         "My name is <<PII_PERSON_3ab…>>, email <<PII_EMAIL_ADDRESS_7ce…>>"
                          ↓  LLM responds
From LLM:       "Hello <<PII_PERSON_3ab…>>! I'll contact you at <<PII_EMAIL_ADDRESS_7ce…>>"
                          ↓  stage 09: restore
Client gets:    "Hello Alice Smith! I'll contact you at alice@example.com"
```

The placeholder `<<PII_ENTITY_TYPE_request-local-id>>` is:
- **Deterministic** — same input value always produces the same placeholder within a request
- **Reversible** — the mapping is stored in request context and used to restore values in the response
- **Opaque** — the identifier is a random 128-bit value and contains no original PII
- **Collision-resistant** — distinct values of the same entity type receive distinct placeholders

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
| `INTERNAL_SECRET` | OpenAI-style keys, GitHub tokens, Bearer tokens |

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
    - INTERNAL_SECRET
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

## Coverage and code handling

Relay scans system/developer instructions, message text, typed text blocks, tool-call arguments, Responses API function arguments, code blocks, and git diffs. Content format never bypasses scrubbing. Add stable class names or product terms to `pii.allow_list` when the NER model produces a known false positive.

Caller-originated values use reversible placeholders. PII found in retrieved knowledge-base context is instead replaced with an irreversible `<<REDACTED_ENTITY>>` marker and is never added to the response restoration map.
High-confidence `INTERNAL_SECRET` matches are also irreversible in caller content, so a provider token cannot be
reintroduced if a model echoes its marker.

## Engine

Detection uses **Microsoft Presidio** with a configurable spaCy backend (`en_core_web_sm` by default). The model is loaded on startup, so readiness may take longer than liveness during a cold start.

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
