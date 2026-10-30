# Ring discovery

## Method

One hop from 20 seed cards through device profiles gated at 20 cards, a 30-day window either side, then connected components client-side. A component is a ring only if it holds 3 to 40 cards across two or more customers with two or more of them already confirmed fraudulent by the bank.

## Why one hop and not two

Two hops from the same seeds, with every profile gated the same way, reaches **1,380 cards across 1,360 customers** as one connected set. That is not twenty rings, it is the observation that this device graph has a giant component: a card shares a specific fingerprint with a handful of others, each of those shares a different specific fingerprint with a handful more, and two hops is enough to join most of the graph. Two-hop co-occurrence is therefore not evidence of anything, and the gate that makes one hop meaningful does not survive a second.

This is the same failure the specificity gate exists for, one hop further out. 116 of the 9,704 profiles carry 24,653 of the card links and the largest spans 842 cards; gating at twenty fixes the first hop and not the second.

## What one hop found

**No component of 3 or more cards.** That is a finding, not a gap: with the specificity gate applied, the twenty benchmark cards do not sit in a shared-device ring, and R6 is correct not to fire on them.
