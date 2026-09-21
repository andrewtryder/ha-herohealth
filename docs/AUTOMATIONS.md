# Automation examples

Hero Health entities can be used in normal Home Assistant automations. The examples
below are intended as starting points; entity IDs can vary based on the dispenser's
device name and Home Assistant entity naming.

## Low-medication announcements

The low-medications sensor has two different kinds of data:

- The **sensor state** is the number of medications currently reporting low.
- The **`medications` attribute** is the list of medication names currently
  reporting low.
- The **`slots` attribute** is the list of affected Hero slot numbers.

For example, a sensor state of `1` means one medication is low. To speak or display
the medication name, use:

```jinja
{{ state_attr('sensor.hero_health_low_medications', 'medications') | join(', ') }}
```

Do not use only `states('sensor.hero_health_low_medications')` when you want the
names; that returns the numeric count.

Your actual entity ID may be different, such as
`sensor.hero_dispenser_low_medications`. Confirm it in **Developer Tools → States**
before copying an example.

### Suggested twice-daily announcement

The example below:

1. requests a refresh 15 minutes before each announcement,
2. refreshes once more immediately before speaking,
3. says nothing when no medications are low, and
4. reads medication names from the `medications` attribute rather than speaking
   only the numeric sensor state.

The included notification action uses the Home Assistant Google Assistant SDK
notification service. Replace that action if you use a different TTS or notification
integration.

A copyable version is also available at
[`examples/low_medication_announcement.yaml`](../examples/low_medication_announcement.yaml).

```yaml
alias: Medication Level Check and Announcement
description: >-
  Refreshes Hero Health 15 minutes prior, then announces low medications at 7:30
  AM and 6:30 PM.
triggers:
  - trigger: time
    at: "07:15:00"
    id: refresh_only
  - trigger: time
    at: "18:15:00"
    id: refresh_only
  - trigger: time
    at: "07:30:00"
    id: announce
  - trigger: time
    at: "18:30:00"
    id: announce

conditions: []

actions:
  - choose:
      - conditions:
          - condition: trigger
            id: refresh_only
        sequence:
          - action: homeassistant.update_entity
            target:
              entity_id: sensor.hero_health_low_medications

      - conditions:
          - condition: trigger
            id: announce
        sequence:
          - action: homeassistant.update_entity
            target:
              entity_id: sensor.hero_health_low_medications

          - delay: "00:00:02"

          - condition: template
            value_template: >-
              {% set value = states('sensor.hero_health_low_medications') %}
              {{ value not in ['unknown', 'unavailable', 'none', '']
                 and value | int(0) > 0 }}

          - action: notify.google_assistant_sdk
            data:
              title: Medication Alert
              message: >-
                {% set entity = 'sensor.hero_health_low_medications' %}
                {% set meds = state_attr(entity, 'medications') or [] %}
                {% set count = states(entity) | int(0) %}
                {% if meds | length > 0 %}
                  {% if count == 1 %}
                    Attention. One medication is running low:
                    {{ meds | join(', ') }}.
                  {% else %}
                    Attention. {{ count }} medications are running low:
                    {{ meds | join(', ') }}.
                  {% endif %}
                {% elif count == 1 %}
                  Attention. One medication is reporting low.
                {% else %}
                  Attention. {{ count }} medications are reporting low.
                {% endif %}

mode: single
```

### Why the refresh is repeated

The early refresh gives Hero time to update before the scheduled announcement. The
second refresh immediately before the announcement reduces the chance of speaking a
stale level if the dispenser state changed during those 15 minutes.

The short delay allows the entity update to propagate before the template condition
and message are evaluated.
