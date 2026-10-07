package rectkernel.state;

/**
 * Presence marker of a rectangle during solving.
 * UNDECIDED rectangles are optional rectangles whose existence is not yet
 * decided; they must never be treated as mandatory by propagation.
 */
public enum Presence {
    PRESENT,
    ABSENT,
    UNDECIDED
}
