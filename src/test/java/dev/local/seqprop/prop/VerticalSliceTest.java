package dev.local.seqprop.prop;

import java.util.List;
import java.util.SortedSet;
import java.util.TreeSet;
import dev.local.seqprop.diag.Diag;
import dev.local.seqprop.diag.FailureCategory;
import dev.local.seqprop.model.CspModel;
import dev.local.seqprop.model.EndMode;
import dev.local.seqprop.model.SequenceConstraint;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class VerticalSliceTest {

  private CspModel model(int lo, int hi) {
    CspModel model = new CspModel(3, List.of(0, 1));
    model.addConstraint(new SequenceConstraint("seq", 0, 2, 2, lo, hi, new TreeSet<>(List.of(1)),
        EndMode.FULL_ONLY));
    return model;
  }

  @Test
  void jointOverlapContradictionIsDetected() {
    CspModel model = model(1, 1);
    DomainState domains = new DomainState(model.universe(), model.n());
    domains.assign(0, domains.indexOfValue(1)); // x0 = 1
    domains.assign(2, domains.indexOfValue(1)); // x2 = 1

    PropagationResult result =
        new PropagationEngine(model, new Diag("req-slice-1", false), false).propagate(domains);

    assertFalse(result.consistent(), "each window can still count one locally, but the two "
        + "forced target positions force both overlapping windows to contain one each only "
        + "if x1=0 for [0,1] and x1=1 for [1,2]: jointly impossible");
    assertEquals(FailureCategory.JOINT_OVERLAP_CONTRADICTION, result.failureCategory());
  }

  @Test
  void consistentInstanceReachesFixpoint() {
    CspModel model = model(1, 1);
    DomainState domains = new DomainState(model.universe(), model.n());
    domains.assign(0, domains.indexOfValue(1));

    PropagationResult result =
        new PropagationEngine(model, new Diag("req-slice-2", false), false).propagate(domains);

    assertTrue(result.consistent());
    assertEquals(FailureCategory.NONE, result.failureCategory());
  }
}
