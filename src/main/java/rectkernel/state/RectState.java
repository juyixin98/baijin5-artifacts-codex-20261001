package rectkernel.state;

import rectkernel.model.Domain;
import rectkernel.model.RectSpec;

/** Mutable per-rectangle solving state: presence marker plus position domain. */
public final class RectState {

    private final RectSpec spec;
    private Presence presence;
    private Domain domain;

    public RectState(RectSpec spec) {
        this.spec = spec;
        this.presence = spec.optional() ? Presence.UNDECIDED : Presence.PRESENT;
        this.domain = spec.initialDomain();
    }

    private RectState(RectSpec spec, Presence presence, Domain domain) {
        this.spec = spec;
        this.presence = presence;
        this.domain = domain;
    }

    public RectState copy() {
        return new RectState(spec, presence, domain);
    }

    public RectSpec spec() {
        return spec;
    }

    public Presence presence() {
        return presence;
    }

    public void setPresence(Presence presence) {
        this.presence = presence;
    }

    public Domain domain() {
        return domain;
    }

    public void setDomain(Domain domain) {
        this.domain = domain;
    }
}
