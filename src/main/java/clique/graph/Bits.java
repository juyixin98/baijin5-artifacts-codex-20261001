package clique.graph;

import java.math.BigInteger;
import java.util.Iterator;

/** BigInteger 位集的升序迭代。BigInteger 不可变，统一在此维护"清除已访问位"的游标。 */
public final class Bits {
    private Bits() {
    }

    public static Iterable<Integer> setBits(BigInteger bits) {
        return () -> new Iterator<>() {
            private BigInteger rest = bits;

            @Override
            public boolean hasNext() {
                return rest.signum() != 0;
            }

            @Override
            public Integer next() {
                int v = rest.getLowestSetBit();
                rest = rest.clearBit(v);
                return v;
            }
        };
    }
}
