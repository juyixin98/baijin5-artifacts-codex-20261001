package rectkernel.search;

/** One rectangle's decided outcome: present at (x,y), or absent. */
public record Placement(String id, boolean present, long x, long y) {
}
