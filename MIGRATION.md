# Migrating from cloudflare-hero

Install and validate this integration alongside the existing Worker first. Do not
remove the existing REST package until the new entity has produced expected values.

Replace `sensor.cloudflare_medications_api` with
`sensor.hero_health_low_medications`. Its state is the same compatibility format:
comma-separated medication names, or `None` when no medicines are low.

```yaml
automation:
  - alias: Announce low Hero medications
    trigger:
      - platform: time
        at: "07:15:00"
      - platform: time
        at: "18:15:00"
    action:
      - action: hero_health.refresh
      - delay: "00:15:00"
      - condition: template
        value_template: >-
          {{ states('sensor.hero_health_low_medications') not in
             ['None', 'unknown', 'unavailable'] }}
      - service: tts.speak
        target: {entity_id: tts.your_tts_provider}
        data:
          media_player_entity_id: media_player.your_speaker
          message: "Hero medications are low: {{ states('sensor.hero_health_low_medications') }}"
```

`homeassistant.update_entity` on the low-medications entity also requests a
coordinator refresh. The `hero_health.refresh` action is the explicit, documented
choice and is required if multiple Hero entries need a particular `entry_id`.
