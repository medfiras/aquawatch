<p align="center">
  <img src="https://raw.githubusercontent.com/medfiras/aquawatch/main/brands/custom_integrations/aquawatch/icon.png" width="96" height="96" alt="Icône AquaWatch">
</p>

# AquaWatch

Intégration Home Assistant (HACS) pour le suivi de la consommation d'eau :
détection de fuite/anomalie, prévisions, éco-score, intégration au tableau
de bord Énergie, et une architecture prête pour plusieurs fournisseurs
d'eau français.

## Fournisseurs

- **SEDIF / L'Eau d'Île-de-France** — ✅ Fonctionnel
- **Veolia** — 🚧 Bientôt (contributions bienvenues)
- **Suez** — 🚧 Bientôt (contributions bienvenues)
- **Saur** — 🚧 Bientôt (contributions bienvenues)

## Fonctionnalités

- Consommation quotidienne, index du compteur, coût, prix au m³
- Prévision de consommation et de coût en fin de mois
- Comparaison vs semaine/mois/année précédente
- Éco-score gamifié (0-100, grade A-E) basé sur les repères ADEME
- Détection de fuite (consommation soutenue au-dessus de la baseline sur
  plusieurs jours — l'API SEDIF n'exposant que des totaux journaliers, la
  détection est définie ainsi plutôt que via une fenêtre de débit nocturne)
- Détection d'anomalie statistique (z-score)
- Alerte de budget mensuel (€ ou m³)
- Import automatique de l'historique disponible dans les statistics long
  terme de HA (compatible tableau de bord Énergie)
- Services `force_refresh`, `export_csv`, `recalibrate_baseline`
- Blueprint d'automatisation pour des notifications de fuite actionables
- Exemple de dashboard Lovelace prêt à l'emploi

## Entités

### Capteurs

- **Consommation du jour** (L) — consommation du dernier relevé disponible
- **Consommation de la veille** (L) — consommation du relevé précédent
- **Index du compteur** (m³) — valeur cumulée affichée par le compteur
- **Coût du jour** (€) — coût du dernier relevé (litres × prix au m³)
- **Coût du mois en cours** (€) — coût cumulé depuis le 1er du mois en cours
- **Prix au m³** (€/m³) — prix actuellement appliqué par le fournisseur
- **Prévision volume fin de mois** (m³) — projection du volume total en fin de mois, basée sur la consommation actuelle
- **Prévision coût fin de mois** (€) — projection du coût total en fin de mois
- **Variation vs semaine précédente** (%) — écart de consommation par rapport aux 7 jours précédents
- **Variation vs mois précédent** (%) — écart de consommation par rapport aux 30 jours précédents
- **Variation vs année précédente** (%) — écart par rapport à la même période l'an dernier (nécessite ~2 ans d'historique)
- **Éco-score** (pts) — score gamifié basé sur la consommation par personne, avec les attributs `grade` (A-E) et `conseil`
- **Dernière synchronisation** *(diagnostic)* — date du dernier relevé récupéré avec succès
- **Solde du compte** (€) — solde du compte client tel que rapporté par le fournisseur

Le capteur **Index du compteur** porte aussi des attributs supplémentaires :
`numero_contrat`, `numero_serie_compteur`, `adresse` (point de livraison) et
`statut_contrat`.

### Capteurs binaires

- **Fuite suspectée** — consommation quotidienne soutenue au-dessus de la baseline sur plusieurs jours consécutifs
- **Anomalie détectée** — écart statistique (z-score) par rapport à la moyenne glissante des 14 derniers jours
- **Budget dépassé** — la projection de fin de mois dépasse le budget configuré (€ ou m³)
- **Données périmées** *(diagnostic)* — aucun nouveau relevé reçu depuis plusieurs jours

## Installation

### Via HACS

1. HACS > Intégrations > ⋮ > Dépôts personnalisés > ajouter ce dépôt
2. Installer "AquaWatch"
3. Redémarrer Home Assistant
4. Paramètres > Appareils et services > Ajouter une intégration > AquaWatch

### Manuelle

Copier `custom_components/aquawatch` dans le dossier `custom_components` de
votre configuration HA, puis redémarrer.

## Configuration

Le flux de configuration demande : le fournisseur, l'email/mot de passe du
compte, puis le compteur à suivre si le compte en a plusieurs. Les seuils de
détection, la fréquence de rafraîchissement, le budget et la taille du foyer
se règlent ensuite depuis Paramètres > Appareils et services > AquaWatch >
Configurer.

## Notifications

L'intégration ne notifie pas directement — elle expose des `binary_sensor`
et des events HA. Importez le blueprint fourni
(`blueprints/automation/aquawatch/leak_notification.yaml`) pour recevoir une
notification mobile actionable en cas de fuite suspectée.

## Licence

Apache-2.0.
