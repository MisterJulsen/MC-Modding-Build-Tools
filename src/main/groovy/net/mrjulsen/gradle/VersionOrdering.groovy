package net.mrjulsen.gradle

final class VersionOrdering {

    private VersionOrdering() {}

    private static final List<String> QUALIFIERS =
            ['alpha', 'beta', 'milestone', 'rc', 'snapshot', '', 'sp'].asImmutable()

    private static final String RELEASE_INDEX = String.valueOf(QUALIFIERS.indexOf(''))

    private static final Map<String, String> ALIASES =
            ['ga': '', 'final': '', 'release': '', 'cr': 'rc'].asImmutable()

    static int compareMaven(String left, String right) {
        return compareItems(parseMaven(left), parseMaven(right))
    }

    private static List parseMaven(String version) {
        String value = version.toLowerCase(Locale.ENGLISH)

        List root = []
        List<List> stack = [root]
        List current = root

        boolean isDigit = false
        int startIndex = 0

        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i)

            if (c == ('.' as char)) {
                if (i == startIndex) {
                    current.add(BigInteger.ZERO)
                } else {
                    current.add(parseItem(isDigit, value.substring(startIndex, i)))
                }
                startIndex = i + 1

            } else if (c == ('-' as char)) {
                if (i == startIndex) {
                    current.add(BigInteger.ZERO)
                } else {
                    current.add(parseItem(isDigit, value.substring(startIndex, i)))
                }
                startIndex = i + 1
                current = pushSublist(current, stack)

            } else if (Character.isDigit(c)) {
                if (!isDigit && i > startIndex) {
                    current.add(stringItem(value.substring(startIndex, i), true))
                    startIndex = i
                    current = pushSublist(current, stack)
                }
                isDigit = true

            } else {
                if (isDigit && i > startIndex) {
                    current.add(parseItem(true, value.substring(startIndex, i)))
                    startIndex = i
                    current = pushSublist(current, stack)
                }
                isDigit = false
            }
        }

        if (value.length() > startIndex) {
            current.add(parseItem(isDigit, value.substring(startIndex)))
        }

        while (!stack.isEmpty()) {
            normalize(stack.remove(stack.size() - 1))
        }

        return root
    }

    private static List pushSublist(List current, List<List> stack) {
        List sublist = []
        current.add(sublist)
        stack.add(sublist)
        return sublist
    }

    private static Object parseItem(boolean isDigit, String buffer) {
        if (isDigit) {
            String stripped = buffer.replaceFirst(/^0+/, '')
            return stripped.isEmpty() ? BigInteger.ZERO : new BigInteger(stripped)
        }
        return stringItem(buffer, false)
    }

    private static String stringItem(String raw, boolean followedByDigit) {
        String value = raw

        if (followedByDigit && value.length() == 1) {
            switch (value.charAt(0)) {
                case 'a' as char: value = 'alpha'; break
                case 'b' as char: value = 'beta'; break
                case 'm' as char: value = 'milestone'; break
                default: break
            }
        }

        return ALIASES.containsKey(value) ? ALIASES.get(value) : value
    }

    private static String comparableQualifier(String qualifier) {
        int index = QUALIFIERS.indexOf(qualifier)
        return index == -1 ? "${QUALIFIERS.size()}-${qualifier}".toString() : String.valueOf(index)
    }

    private static void normalize(List list) {
        for (int i = list.size() - 1; i >= 0; i--) {
            Object item = list.get(i)
            if (isNullItem(item)) {
                list.remove(i)
            } else if (!(item instanceof List)) {
                break
            }
        }
    }

    private static boolean isNullItem(Object item) {
        if (item instanceof BigInteger) return item == BigInteger.ZERO
        if (item instanceof String) return comparableQualifier((String) item) == RELEASE_INDEX
        if (item instanceof List) return ((List) item).isEmpty()
        return false
    }

    private static int compareItems(Object left, Object right) {
        if (left == null && right == null) return 0
        if (left == null) return -compareToAbsent(right)
        if (right == null) return compareToAbsent(left)

        if (left instanceof BigInteger) {
            if (right instanceof BigInteger) return ((BigInteger) left).compareTo((BigInteger) right)
            return 1
        }

        if (left instanceof String) {
            if (right instanceof BigInteger) return -1
            if (right instanceof String) {
                return comparableQualifier((String) left) <=> comparableQualifier((String) right)
            }
            return -1
        }

        if (left instanceof List) {
            if (right instanceof BigInteger) return -1
            if (right instanceof String) return 1
            return compareLists((List) left, (List) right)
        }

        return 0
    }

    private static int compareToAbsent(Object item) {
        if (item instanceof BigInteger) return item == BigInteger.ZERO ? 0 : 1
        if (item instanceof String) return comparableQualifier((String) item) <=> RELEASE_INDEX
        if (item instanceof List) {
            List list = (List) item
            return list.isEmpty() ? 0 : compareToAbsent(list.get(0))
        }
        return 0
    }

    private static int compareLists(List left, List right) {
        int max = Math.max(left.size(), right.size())
        for (int i = 0; i < max; i++) {
            Object l = i < left.size() ? left.get(i) : null
            Object r = i < right.size() ? right.get(i) : null
            int result = compareItems(l, r)
            if (result != 0) return result
        }
        return 0
    }

    static int compareSemVer(String left, String right) {
        return compareSemVerParts(parseSemVer(left), parseSemVer(right))
    }

    static boolean isSemVerParseable(String version) {
        try {
            parseSemVer(version)
            return true
        } catch (IllegalArgumentException ignored) {
            return false
        }
    }

    private static Map parseSemVer(String version) {
        if (version == null || version.trim().isEmpty()) {
            throw new IllegalArgumentException("empty version")
        }

        String value = version.trim()

        int plus = value.indexOf('+')
        if (plus >= 0) value = value.substring(0, plus)

        String prerelease = null
        int hyphen = value.indexOf('-')
        if (hyphen >= 0) {
            prerelease = value.substring(hyphen + 1)
            value = value.substring(0, hyphen)
        }

        if (value.isEmpty()) {
            throw new IllegalArgumentException("'${version}' has no numeric component")
        }

        List<Integer> numbers = []
        for (String part : value.split('\\.', -1)) {
            if (!(part ==~ /^\d+$/)) {
                throw new IllegalArgumentException(
                        "'${version}' is not a semantic version: '${part}' is not a number")
            }
            numbers.add(Integer.parseInt(part))
        }

        if (prerelease != null) {
            if (prerelease.isEmpty()) {
                throw new IllegalArgumentException("'${version}' has an empty prerelease part")
            }
            for (String identifier : prerelease.split('\\.', -1)) {
                if (identifier.isEmpty() || !(identifier ==~ /^[0-9A-Za-z-]+$/)) {
                    throw new IllegalArgumentException(
                            "'${version}' has an invalid prerelease identifier '${identifier}'")
                }
            }
        }

        return [numbers: numbers, prerelease: prerelease]
    }

    private static int compareSemVerParts(Map left, Map right) {
        List<Integer> leftNumbers = (List<Integer>) left.numbers
        List<Integer> rightNumbers = (List<Integer>) right.numbers

        int max = Math.max(leftNumbers.size(), rightNumbers.size())
        for (int i = 0; i < max; i++) {
            int l = i < leftNumbers.size() ? leftNumbers.get(i) : 0
            int r = i < rightNumbers.size() ? rightNumbers.get(i) : 0
            int result = Integer.compare(l, r)
            if (result != 0) return result
        }

        String leftPre = (String) left.prerelease
        String rightPre = (String) right.prerelease

        if (leftPre == null && rightPre == null) return 0
        if (leftPre == null) return 1
        if (rightPre == null) return -1

        return comparePrerelease(leftPre, rightPre)
    }

    private static int comparePrerelease(String left, String right) {
        String[] leftIds = left.split('\\.', -1)
        String[] rightIds = right.split('\\.', -1)

        int max = Math.max(leftIds.length, rightIds.length)
        for (int i = 0; i < max; i++) {
            if (i >= leftIds.length) return -1
            if (i >= rightIds.length) return 1

            String l = leftIds[i]
            String r = rightIds[i]

            boolean leftNumeric = l ==~ /^\d+$/
            boolean rightNumeric = r ==~ /^\d+$/

            int result
            if (leftNumeric && rightNumeric) {
                result = new BigInteger(l) <=> new BigInteger(r)
            } else if (leftNumeric) {
                result = -1
            } else if (rightNumeric) {
                result = 1
            } else {
                result = l <=> r
            }

            if (result != 0) return result
        }

        return 0
    }

    static List<String> findOrderingProblems(String previous, String candidate) {
        List<String> problems = []

        int maven = compareMaven(previous, candidate)
        if (maven >= 0) {
            problems.add("Forge/NeoForge (Maven): '${candidate}' does not rank above '${previous}' " +
                    "(comparison yields ${maven == 0 ? 'equal' : 'lower'}). " +
                    "Already published mods depending on '${previous}' or newer would report " +
                    "it as missing.")
        }

        if (!isSemVerParseable(previous)) {
            problems.add("Fabric: the previous version '${previous}' is not a semantic version, " +
                    "so Fabric never compared it numerically. Ranges against it were already " +
                    "unreliable; verify affected mods by hand.")
        } else if (!isSemVerParseable(candidate)) {
            problems.add("Fabric: '${candidate}' is not a semantic version. Fabric falls back to " +
                    "string comparison and every range predicate against this mod stops matching.")
        } else {
            int semver = compareSemVer(previous, candidate)
            if (semver >= 0) {
                problems.add("Fabric (SemVer): '${candidate}' does not rank above '${previous}' " +
                        "(comparison yields ${semver == 0 ? 'equal' : 'lower'}). " +
                        "Already published mods depending on '${previous}' or newer would report " +
                        "it as missing.")
            }
        }

        return problems
    }
}
