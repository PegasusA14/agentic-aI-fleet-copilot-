/** Questions the replay can answer for every company. The composer's menu offers a longer list. */
export const SUGGESTIONS: readonly string[] = [
  "Which devices are low on disk space?",
  "Which Macs run a macOS version older than 15?",
  "Which devices fail a compliance check?",
  "Which devices are using too much memory right now?",
  "Which devices have had low disk space all week?",
  "Which laptops show battery wear?",
  "How many Macs do we have, by model?",
];

/** The three on the empty home screen: short enough to read at a glance, and each one the replay can answer. */
export const HOME_SUGGESTIONS: readonly string[] = [
  "Which devices are low on disk?",
  "Which Macs need a macOS update?",
  "Which devices fail a compliance check?",
];
