"""Answers to viewer questions about the item on screen, from seller-approved card data only.

questions.py reads what a comment asks (price, policy, stock, a missing size), facts.py takes the approved facts of
a card, reply.py writes the sentence and re-checks it against the card state of the moment, selector.py groups
questions over time for a spoken reply. These four modules use the standard library only.
"""
