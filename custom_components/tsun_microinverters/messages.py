"""Message templates ported from the Homey app locales (en, fr). Placeholders: {name}."""

MESSAGES = {'en': {'status': {'normal': 'Normal',
                   'standby': 'Standby (night)',
                   'alarm': 'Alarm',
                   'fault': 'Fault',
                   'code': 'Code',
                   'unreachable': 'Unreachable'},
        'warning': {'standby': 'Inverter in standby or unreachable (normal at night)',
                    'unreachable': 'Inverter unreachable while it was producing: check the logger Wi-Fi (see the app diagnostic log)'},
        'diag': {'app_started': 'App started, version',
                 'level_changed': 'Log level:',
                 'log_cleared': 'Log cleared',
                 'device_started': 'Device started, logger',
                 'update_failed': 'Update failed:',
                 'cause': {'unreachable': 'logger unreachable on the network (Wi-Fi drop, logger switched off or IP address changed)',
                           'port_closed': 'logger reachable but port 8899 refused (logger restarting or firmware change)',
                           'no_response': 'connected to the logger but no answer in time (logger busy, or weak Wi-Fi)',
                           'closed': 'connection closed by the logger during the read (unstable Wi-Fi or logger busy with the cloud)',
                           'inverter_silent': 'logger busy or inverter not answering on the logger–inverter link (collision with the TSUN '
                                              'cloud polling, or inverter stopped)',
                           'corrupt': 'corrupted answer on the logger–inverter link (collision with the TSUN cloud polling)',
                           'serial': 'wrong logger serial number in the device settings',
                           'other': 'unexpected error'},
                 'read_fail_day': 'Read failed while the inverter was producing {power} W: {cause} [{code}]',
                 'read_fail_night': 'Read failed, inverter probably stopped: {cause} [{code}]',
                 'read_fail_detail': 'Failure #{n}: {cause} [{code}] {message}',
                 'comm_lost': 'Inverter unreachable for {minutes} min during production: {cause}',
                 'comm_restored': 'Communication restored after {minutes} min ({count} failed reads, cause: {cause})',
                 'micro_cut': 'Short communication drop of {minutes} min ({count} failed reads, cause: {cause})',
                 'wake': 'Inverter awake at {time} after {hours} h of standby',
                 'sleep': 'Inverter in standby at {time} (end of production, last power {power} W)',
                 'slow': 'Slow logger connection: {connect} ms to connect ({ms} ms in total), weak Wi-Fi signal likely',
                 'poll_ok': 'OK {ms} ms (connection {connect} ms) | AC {power} W, {voltage} V, {temp} °C | PV {panels} W',
                 'status': 'Inverter status: {status}',
                 'alarm': 'Inverter reports {status}',
                 'voltage_high': 'Grid voltage high: {voltage} V (limit {limit} V), the inverter may disconnect',
                 'voltage_low': 'Grid voltage low: {voltage} V (limit {limit} V)',
                 'freq': 'Grid frequency out of range: {freq} Hz',
                 'temp': 'High inverter temperature: {temp} °C (limit {limit} °C)',
                 'efficiency': 'Low conversion efficiency: {eff} % (DC {dc} W, AC {ac} W)',
                 'panel_zero': 'Panel {panel} is not producing: {power} W while the others average {avg} W. {hint}',
                 'panel_zero_hint_v0': 'Panel voltage 0 V: panel, cable or connector disconnected.',
                 'panel_zero_hint_v': 'Panel voltage {voltage} V present: inverter input fault or panel fully shaded.',
                 'panel_under': 'Panel {panel} underperforming: {power} W vs {avg} W for the others ({dev} %)',
                 'panel_ok': 'Panel {panel} back to normal',
                 'day_reset': 'New day {day}: daily counters reset',
                 'counter_reset': 'Inverter daily counter reset detected',
                 'summary': 'Summary {day}: {energy} kWh (panels {panels} kWh), peak {peak} W at {peakTime}, active {wake} to {sleep}, '
                            'grid {vmin} to {vmax} V, max temperature {tmax} °C, reads {ok} OK / {fail} failed ({rate} % OK during '
                            'production), response time avg {lavg} ms / max {lmax} ms, retries {retries} (corrupted {crc}, no answer '
                            '{silent}), isolated failures {isolated}, slow answers (logger busy) {slow}, drops during production: '
                            '{outages} ({outageMin} min)',
                 'settings_changed': 'Settings changed: {keys}',
                 'report_exported': 'Diagnostic report exported',
                 'stats_restored': "Today's statistics restored after a restart ({ok} reads OK, {fail} failed)",
                 'panel_under_adaptive': 'Panel {panel} below its usual production: {power} W for {expected} W expected at this time '
                                         '({dev} %). Probably a shadow: checked in the evening verdict',
                 'panel_daily': 'On {day}, panel {panel} produced {energy} kWh, {dev} % below its usual share (expected about {expected} '
                                'kWh). For information: varies with clouds and seasonal shadows',
                 'daily_learning': 'Daily panel profile being learned (day {days} of {min})',
                 'baseline_reset': 'Learned panel profile reset',
                 'slow_busy': 'Slow answer: {ms} ms with a fast connection ({connect} ms): logger busy with the inverter, not a Wi-Fi '
                              'issue',
                 'retry': 'Retry #{attempt} after a transient error: {cause} [{code}]',
                 'retry_rate': 'Logger–inverter link disturbed: {pct} % of the reads over the last hour needed a retry or failed ({n} of '
                               '{total})',
                 'isolated': 'Isolated failed read, recovered at the next cycle ({cause})',
                 'read_fail_repeated': '{count} consecutive failed reads while the inverter was producing {power} W: {cause} [{code}]',
                 'temp_default_changed': 'Temperature alert raised from 80 to {limit} °C (new default: 80 °C is normal for this inverter '
                                         'on sunny days)',
                 'startup_grace': 'Inverter unreachable for {minutes} min just after the app started (Homey or network restarting): no '
                                  'alert yet',
                 'derating': 'Probable thermal derating at {temp} °C: DC power down from {before} to {after} W and capped for 5 min, while '
                             'the panel voltage rose clearly on {panels} panels',
                 'forecast_updated': 'Forecast updated: today {today} kWh (remaining {remaining} kWh), tomorrow {tomorrow} kWh',
                 'forecast_failed': 'Weather forecast download failed: {message}. Next attempt in 15 min.',
                 'forecast_restored': 'Weather forecast downloaded again',
                 'forecast_no_location': 'Homey location unknown (set it in Homey settings → Location)',
                 'forecast_learn': 'Calibration {hour}h: {actual} kWh produced, {forecast} kWh forecast, factor now {factor}',
                 'forecast_day': 'Forecast of {day} (made the day before): {forecast} kWh, produced {actual} kWh ({error} %); average '
                                 'error over {days} days: {mape} %',
                 'forecast_day_dull': 'Forecast of {day} (made the day before): {forecast} kWh, produced {actual} kWh (day too dull to '
                                      'assess the accuracy)',
                 'forecast_recalibrate': 'Panel plane or peak power changed: forecast calibration restarted',
                 'counter_back': "The inverter daily counter went back to {raw} kWh (evening restart of the inverter): the day's yield "
                                 '{kept} kWh is kept',
                 'standby_start': 'Inverter not answering since the app started: considered in night standby (end of production time not '
                                  'changed)',
                 'delay_default_changed': 'Underperformance minimum duration raised from 15 to {minutes} min (new default: shadows often '
                                          'take longer to cross a panel)',
                 'baseline_migrated': 'Panel profile upgraded: learning restarts in solar time (no more one-hour shift at summer/winter '
                                      'time changes), only in direct sun. Underperformance alerts resume after 3 sunny days; the daily '
                                      'check is kept.',
                 'verdict_under': 'Panel {panel}: on {day}, in direct sun, it did not exceed {best} % of the best panel (usually {usual} '
                                  '%, {dev} % less, threshold {threshold} %). A shadow only lowers a panel for part of the day: worth '
                                  'checking (dirt, cable, diode, inverter input)',
                 'verdict_accepted': 'Panel {panel}: drop confirmed on {days} sunny days in a row ({best} % of the best panel instead of '
                                     '{usual} %). Accepted as the new normal, no more alerts at this level',
                 'verdict_recovered': 'Panel {panel}: production back to normal on {day} ({best} % of the best panel)',
                 'verdict_ok': 'Panel verdict for {day}: all normal',
                 'verdict_cloudy': 'Panel verdict for {day}: not enough direct sun to judge',
                 'verdict_learning': 'Panel verdict being learned (sunny day {days} of {min})',
                 'alarm_seen': 'Inverter reports {status} (notification if it lasts more than 5 min)',
                 'alarm_short': 'Short inverter alarm over ({status}), not notified',
                 'derating_watch': 'Power drop at {temp} °C ({before} → {after} W) with the voltage rising on {panels} panels: watching '
                                   'for a derating plateau',
                 'derating_cloud': 'Power drop at {temp} °C ({before} → {after} W) not followed by a plateau: passing cloud, not derating'},
        'widget': {'today': 'Today',
                   'total': 'Total',
                   'peak': 'Peak today',
                   'grid': 'Grid',
                   'inverter': 'Inverter',
                   'efficiency': 'Efficiency DC→AC',
                   'wifi': 'Logger link',
                   'updated': 'Updated',
                   'of': 'of',
                   'expected': 'usual production at this time',
                   'no_device': 'No TSUN inverter added yet',
                   'dc': 'Panels (DC)',
                   'forecast': 'Forecast (left / day)'}},
 'fr': {'status': {'normal': 'Normal',
                   'standby': 'Veille (nuit)',
                   'alarm': 'Alarme',
                   'fault': 'Défaut',
                   'code': 'Code',
                   'unreachable': 'Injoignable'},
        'warning': {'standby': 'Onduleur en veille ou injoignable (normal la nuit)',
                    'unreachable': "Onduleur injoignable alors qu'il produisait : vérifier le Wi-Fi du logger (voir le journal de "
                                   "diagnostic de l'app)"},
        'diag': {'app_started': 'App démarrée, version',
                 'level_changed': 'Niveau de journal :',
                 'log_cleared': 'Journal effacé',
                 'device_started': 'Appareil démarré, logger',
                 'update_failed': 'Échec de la mise à jour :',
                 'cause': {'unreachable': 'logger injoignable sur le réseau (coupure Wi-Fi, logger éteint ou adresse IP changée)',
                           'port_closed': 'logger joignable mais port 8899 refusé (redémarrage du logger ou changement de firmware)',
                           'no_response': 'connecté au logger mais sans réponse à temps (logger occupé, ou Wi-Fi faible)',
                           'closed': 'connexion fermée par le logger pendant la lecture (Wi-Fi instable ou logger occupé avec le cloud)',
                           'inverter_silent': 'logger occupé ou onduleur sans réponse sur la liaison logger–onduleur (collision avec '
                                              "l'interrogation du cloud TSUN, ou onduleur à l'arrêt)",
                           'corrupt': "réponse corrompue sur la liaison logger–onduleur (collision avec l'interrogation du cloud TSUN)",
                           'serial': 'numéro de série du logger incorrect dans les réglages',
                           'other': 'erreur inattendue'},
                 'read_fail_day': "Échec de lecture alors que l'onduleur produisait {power} W : {cause} [{code}]",
                 'read_fail_night': "Échec de lecture, onduleur probablement à l'arrêt : {cause} [{code}]",
                 'read_fail_detail': 'Échec n° {n} : {cause} [{code}] {message}',
                 'comm_lost': 'Onduleur injoignable depuis {minutes} min en cours de production : {cause}',
                 'comm_restored': 'Communication rétablie après {minutes} min ({count} lectures en échec, cause : {cause})',
                 'micro_cut': 'Brève coupure de communication de {minutes} min ({count} lectures en échec, cause : {cause})',
                 'wake': 'Onduleur réveillé à {time} après {hours} h de veille',
                 'sleep': 'Onduleur en veille à {time} (fin de production, dernière puissance {power} W)',
                 'slow': 'Connexion lente au logger : {connect} ms pour se connecter ({ms} ms au total), signal Wi-Fi faible probable',
                 'poll_ok': 'OK {ms} ms (connexion {connect} ms) | AC {power} W, {voltage} V, {temp} °C | PV {panels} W',
                 'status': 'Statut onduleur : {status}',
                 'alarm': "L'onduleur signale : {status}",
                 'voltage_high': "Tension réseau élevée : {voltage} V (limite {limit} V), risque de décrochage de l'onduleur",
                 'voltage_low': 'Tension réseau basse : {voltage} V (limite {limit} V)',
                 'freq': 'Fréquence réseau hors plage : {freq} Hz',
                 'temp': 'Température onduleur élevée : {temp} °C (limite {limit} °C)',
                 'efficiency': 'Rendement de conversion faible : {eff} % (DC {dc} W, AC {ac} W)',
                 'panel_zero': 'Le panneau {panel} ne produit pas : {power} W alors que les autres produisent {avg} W en moyenne. {hint}',
                 'panel_zero_hint_v0': 'Tension panneau 0 V : panneau, câble ou connecteur débranché.',
                 'panel_zero_hint_v': "Tension panneau présente ({voltage} V) : défaut de l'entrée de l'onduleur ou panneau entièrement "
                                      'ombragé.',
                 'panel_under': 'Panneau {panel} sous-performant : {power} W contre {avg} W pour les autres ({dev} %)',
                 'panel_ok': 'Panneau {panel} revenu à la normale',
                 'day_reset': 'Nouveau jour {day} : compteurs du jour remis à zéro',
                 'counter_reset': "Remise à zéro du compteur du jour de l'onduleur détectée",
                 'summary': 'Bilan du {day} : {energy} kWh (panneaux {panels} kWh), pic {peak} W à {peakTime}, actif de {wake} à {sleep}, '
                            'réseau {vmin} à {vmax} V, température max {tmax} °C, lectures {ok} OK / {fail} en échec ({rate} % OK en '
                            'production), temps de réponse moyen {lavg} ms / max {lmax} ms, nouvelles tentatives {retries} (réponse '
                            'corrompue {crc}, sans réponse {silent}), échecs isolés {isolated}, réponses lentes (logger occupé) {slow}, '
                            'coupures en production : {outages} ({outageMin} min)',
                 'settings_changed': 'Réglages modifiés : {keys}',
                 'report_exported': 'Rapport de diagnostic exporté',
                 'stats_restored': 'Statistiques du jour restaurées après un redémarrage ({ok} lectures OK, {fail} en échec)',
                 'panel_under_adaptive': 'Panneau {panel} sous sa production habituelle : {power} W pour {expected} W attendus à cette '
                                         'heure ({dev} %). Ombre probable : vérifié dans le bilan du soir',
                 'panel_daily': 'Le {day}, le panneau {panel} a produit {energy} kWh, soit {dev} % sous sa part habituelle (attendu '
                                'environ {expected} kWh). Pour information : varie avec les nuages et les ombres saisonnières',
                 'daily_learning': 'Profil journalier des panneaux en apprentissage (jour {days} sur {min})',
                 'baseline_reset': 'Profil appris des panneaux réinitialisé',
                 'slow_busy': "Réponse lente : {ms} ms avec une connexion rapide ({connect} ms) : logger occupé avec l'onduleur, pas un "
                              'problème Wi-Fi',
                 'retry': 'Nouvelle tentative n° {attempt} après une erreur transitoire : {cause} [{code}]',
                 'retry_rate': 'Liaison logger–onduleur perturbée : {pct} % des lectures de la dernière heure ont nécessité une nouvelle '
                               'tentative ou ont échoué ({n} sur {total})',
                 'isolated': 'Échec de lecture isolé, rétabli au cycle suivant ({cause})',
                 'read_fail_repeated': "{count} lectures consécutives en échec alors que l'onduleur produisait {power} W : {cause} "
                                       '[{code}]',
                 'temp_default_changed': 'Alerte température relevée de 80 à {limit} °C (nouvelle valeur par défaut : 80 °C est normal '
                                         'pour cet onduleur par beau temps)',
                 'startup_grace': "Onduleur injoignable depuis {minutes} min juste après le démarrage de l'app (Homey ou réseau en cours "
                                  "de redémarrage) : pas encore d'alerte",
                 'derating': 'Bridage thermique probable à {temp} °C : puissance DC passée de {before} à {after} W et plafonnée pendant 5 '
                             'min, alors que la tension a nettement monté sur {panels} panneaux',
                 'forecast_updated': "Prévision mise à jour : aujourd'hui {today} kWh (restant {remaining} kWh), demain {tomorrow} kWh",
                 'forecast_failed': 'Échec du téléchargement de la prévision météo : {message}. Nouvel essai dans 15 min.',
                 'forecast_restored': 'Prévision météo de nouveau téléchargée',
                 'forecast_no_location': 'Position du Homey inconnue (à régler dans Réglages Homey → Localisation)',
                 'forecast_learn': 'Calibrage {hour}h : {actual} kWh produits, {forecast} kWh prévus, facteur désormais {factor}',
                 'forecast_day': 'Prévision du {day} (faite la veille) : {forecast} kWh, produit {actual} kWh ({error} %) ; erreur moyenne '
                                 'sur {days} jours : {mape} %',
                 'forecast_day_dull': 'Prévision du {day} (faite la veille) : {forecast} kWh, produit {actual} kWh (journée trop sombre '
                                      'pour évaluer la précision)',
                 'forecast_recalibrate': 'Plan des panneaux ou puissance crête modifié : calibrage de la prévision relancé',
                 'counter_back': "Le compteur du jour de l'onduleur est revenu à {raw} kWh (redémarrage de l'onduleur en fin de journée) : "
                                 'la production du jour {kept} kWh est conservée',
                 'standby_start': "Onduleur muet depuis le démarrage de l'app : considéré en veille de nuit (heure de fin de production "
                                  'inchangée)',
                 'delay_default_changed': 'Durée minimale de sous-performance relevée de 15 à {minutes} min (nouvelle valeur par défaut : '
                                          'une ombre met souvent plus longtemps à traverser un panneau)',
                 'baseline_migrated': "Profil des panneaux mis à niveau : l'apprentissage redémarre en heure solaire (plus de décalage "
                                      "d'une heure aux changements d'heure), uniquement en plein soleil. Les alertes de sous-performance "
                                      'reprennent après 3 jours ensoleillés ; le contrôle journalier est conservé.',
                 'verdict_under': "Panneau {panel} : le {day}, en plein soleil, il n'a pas dépassé {best} % du meilleur panneau "
                                  '(habituellement {usual} %, soit {dev} % de moins, seuil {threshold} %). Une ombre ne baisse un panneau '
                                  "qu'une partie de la journée : à vérifier (encrassement, câble, diode, entrée de l'onduleur)",
                 'verdict_accepted': 'Panneau {panel} : baisse confirmée {days} jours de soleil de suite ({best} % du meilleur panneau au '
                                     "lieu de {usual} %). Retenue comme nouvelle normale, plus d'alerte pour ce niveau",
                 'verdict_recovered': 'Panneau {panel} : production revenue à la normale le {day} ({best} % du meilleur panneau)',
                 'verdict_ok': 'Bilan des panneaux du {day} : tous normaux',
                 'verdict_cloudy': 'Bilan des panneaux du {day} : pas assez de plein soleil pour juger',
                 'verdict_learning': 'Bilan des panneaux en apprentissage (jour de soleil {days} sur {min})',
                 'alarm_seen': "L'onduleur signale : {status} (notification si cela dure plus de 5 min)",
                 'alarm_short': "Alarme passagère de l'onduleur terminée ({status}), sans notification",
                 'derating_watch': 'Baisse de puissance à {temp} °C ({before} → {after} W) avec tension en hausse sur {panels} panneaux : '
                                   "surveillance d'un palier de bridage",
                 'derating_cloud': 'Baisse de puissance à {temp} °C ({before} → {after} W) sans palier ensuite : nuage passager, pas de '
                                   'bridage'},
        'widget': {'today': "Aujourd'hui",
                   'total': 'Total',
                   'peak': 'Pic du jour',
                   'grid': 'Réseau',
                   'inverter': 'Onduleur',
                   'efficiency': 'Rendement DC→AC',
                   'wifi': 'Liaison logger',
                   'updated': 'Mis à jour',
                   'of': 'de',
                   'expected': 'production habituelle à cette heure',
                   'no_device': 'Aucun onduleur TSUN ajouté',
                   'dc': 'Panneaux (DC)',
                   'forecast': 'Prévision (reste / jour)'}}}


for _lang, _errors in {'en': {'empty_response': 'Empty response from the inverter',
        'forecast_disabled': 'The production forecast is off: enable it in the device settings (Production forecast).',
        'forecast_unavailable': 'No production forecast available for that day yet (weather forecast not downloaded).',
        'forecast_window': 'Invalid time range: the end must be after the start (00:00 as the end means midnight).',
        'invalid_ip': 'Invalid IP address',
        'invalid_sn': 'Invalid logger serial number (digits only)',
        'read_failed': 'Read failed:',
        'unreachable': 'Logger unreachable. Is the inverter producing right now? Details:'},
 'fr': {'empty_response': "Réponse vide de l'onduleur",
        'forecast_disabled': "La prévision de production est désactivée : activez-la dans les réglages de l'appareil (Prévision "
                             'de production).',
        'forecast_unavailable': 'Pas encore de prévision de production pour ce jour (prévision météo non téléchargée).',
        'forecast_window': 'Plage horaire invalide : la fin doit être après le début (00:00 en fin signifie minuit).',
        'invalid_ip': 'Adresse IP invalide',
        'invalid_sn': 'Numéro de série du logger invalide (chiffres uniquement)',
        'read_failed': 'Échec de la lecture :',
        'unreachable': "Logger injoignable. L'onduleur produit-il en ce moment ? Détail :"}}.items():
    MESSAGES[_lang]['errors'] = _errors


def t(lang: str, key: str, **vars_) -> str:
    """Message lookup by dotted key ('diag.cause.serial') with {placeholder} substitution."""
    node = MESSAGES.get(lang) or MESSAGES['en']
    for part in key.split('.'):
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, str):
        return key
    import re
    return re.sub(r'\{(\w+)\}', lambda m: str(vars_[m.group(1)]) if m.group(1) in vars_ and vars_[m.group(1)] is not None else m.group(0), node)
