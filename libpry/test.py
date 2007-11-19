"""
    A tree-based unit test system.

    Each test is a node in a tree of tests. Each test can have a setUp and
    tearDown method - these get run before and after each child node is run.
"""
import sys, time, traceback, os, fnmatch, config
import tinytree
import coverage

_TestGlob = "test_*.py"


class Skip(Exception): pass


class Error:
    """
        Error tests False.
    """
    def __init__(self, node, msg):
        self.node, self.msg = node, msg
        self.exctype, self.excvalue, self.tb = sys.exc_info()
        # Expunge libpry from the traceback
        while "libpry" in self.tb.tb_frame.f_code.co_filename:
            next = self.tb.tb_next
            if next:
                self.tb = next
            else:
                break
        # We lose information if we call format_exception in __str__
        self.s = traceback.format_exception(
                self.exctype, self.excvalue, self.tb
            )

    def __str__(self):
        strs = [
                 "Error in %s:"%self.msg,
                 "    %s"%self.node.fullPath()
               ]
        for i in self.s:
            strs.append("    %s"%i.rstrip())
        strs.append("\n")
        return "\n".join(strs)


class OK:
    """
       OK tests True.
    """
    def __init__(self, node, time):
        self.node, self.time = node, time

class _OutputZero:
    def nodePre(self, node): return ""
    def nodeError(self, node): return ""
    def nodePass(self, node): return ""
    def final(self, node): return ""


class _OutputOne:
    def nodePre(self, node): return ""

    def nodeError(self, node):
        return "E"

    def nodePass(self, node):
        return "."

    def final(self, root):
        lst = ["\n"]
        for i in root.preOrder():
            if i.isError():
                lst.append(str(i.getError()))
        if root.runState and not root.isError():
            infostr = []
            errs = root.allError()
            if errs:
                infostr.append ("fail: %s"%len(errs))
            lst.append(
                "%s tests %s - %.3fs\n"%(
                    len(root.tests()),
                    "(%s)"%"".join(infostr) if infostr else "",
                    root.runState.time
                )
            )
        else:
            lst.append(
                "No tests run.\n"
            )

        if root.cover:
            lst.append("\n\n")
            lst.append("                    Coverage\n")
            lst.append("                    ========\n")
            lst.append("\n")
            for i in root.preOrder():
                if hasattr(i, "coverage") and i.coverage:
                    lst.append(i.coverage.statStr())
                    #t.coverageSummary()
        return "".join(lst)


class _OutputTwo(_OutputOne):
    def nodePre(self, node):
        return "%s ...\t"%node.fullPath()

    def nodeError(self, node):
        return "FAIL\n"

    def nodePass(self, node):
        return "OK\n"


class _OutputThree(_OutputTwo):
    pass


class _Output:
    def __init__(self, verbosity, fp=sys.stdout):
        self.fp = fp
        if verbosity == 0:
            self.o = _OutputZero()
        elif verbosity == 1:
            self.o = _OutputOne()
        elif verbosity == 2:
            self.o = _OutputTwo()
        elif verbosity == 3:
            self.o = _OutputThree()
        else:
            self.o = _OutputThree()

    def __getattr__(self, attr):
        meth = getattr(self.o, attr)
        def printClosure(*args, **kwargs):
            if self.fp:
                self.fp.write(meth(*args, **kwargs))
        return printClosure


class _TestBase(tinytree.Tree):
    """
        Automatically turns methods or arbitrary callables of the form test_*
        into TestNodes.
    """
    # The name of this node. Names should not contain periods or spaces.
    name = None
    _selected = True
    def __init__(self, children=None, name=None):
        tinytree.Tree.__init__(self, children)
        if name:
            self.name = name
        self._ns = {}

    def __getitem__(self, key):
        if self._ns.has_key(key):
            return self._ns[key]
        elif self.parent:
            return self.parent.__getitem__(key)
        else:
            raise KeyError, "No such data item: %s"%key

    def __setitem__(self, key, value):
        self._ns[key] = value

    def _run(self, srcObj, dstObj, meth, *args, **kwargs):
        """
            Utility method that runs a callable, and returns a State object.
            
            srcObj:     The object from which to source the method
            dstObj:     The object on which to set the state variable
            meth:       The name of the method to call
        """
        if meth == "__call__":
            c = self
            meth = "call"
        else:
            c = getattr(srcObj, meth, None)
        if not c:
            return None
        try:
            start = time.time()
            c(*args, **kwargs)
            stop = time.time()
        except Exception, e:
            setattr(dstObj, meth + "State", Error(srcObj, meth))
            raise Skip()
        setattr(dstObj, meth + "State", OK(srcObj, stop-start))

    def tests(self):
        """
            All test nodes.
        """
        lst = []
        for i in self.preOrder():
            if isinstance(i, TestNode):
                lst.append(i)
        return lst

    def special(self):
        yield self

    def hasTests(self):
        """
            Does this node have currently selected child tests?
        """
        for i in self.preOrder():
            if isinstance(i, TestNode) and i._selected:
                 return True
        return False

    def prune(self):
        """
            Remove all internal nodes that have no test children.
        """
        for i in self.postOrder():
            if not i.hasTests() and i.parent:
                i.remove()

    def allError(self):
        """
            All test nodes that errored.
        """
        return [i for i in self.tests() if i.isError()]

    def allPassed(self):
        """
            All test nodes that passed.
        """
        return [i for i in self.tests() if i.isPassed()]

    def allNotRun(self):
        """
            All test nodes that that have not been run.
        """
        return [i for i in self.tests() if i.isNotRun()]

    def isError(self):
        """
            True if this node has experienced a test failure.
        """
        for i in self._states():
            if isinstance(i, Error):
                return True
        return False

    def getError(self):
        """
            Return the Error object for this node. Raises an exception if there
            is none.
        """
        for i in self._states():
            if isinstance(i, Error):
                return i
        raise ValueError, "No error for this node."

    def isNotRun(self):
        """
            True if this node has experienced a test failure.
        """
        for i in self._states():
            if not i is None:
                return False
        return True

    def isPassed(self):
        """
            True if this node has passed.
        """
        if (not self.isError()) and (not self.isNotRun()):
            return True
        return False

    def fullPathParts(self):
        """
            Return the components text path of a node.
        """
        lst = []
        for i in self.pathFromRoot():
            if i.name:
                lst.append(i.name)
        return lst

    def fullPath(self):
        """
            Return the full text path of a node as a string.
        """
        return ".".join(self.fullPathParts())

    def search(self, spec):
        """
            Search for matching child nodes using partial path matching.
        """
        # Sneakily (but inefficiently) use string 'in' operator for subsequence
        # searches. This doesn't matter for now, but we can do better.
        xspec = "." + spec + "."
        lst = []
        for i in self.children:
            p = "." + i.fullPath() + "."
            if xspec in p:
                lst.append(i)
            else:
                lst.extend(i.search(spec))
        return lst

    def mark(self, spec):
        """
            - First, un-select all nodes.
            - Now find all matches for spec. For each match, select all direct
              ancestors and all children as
        """
        for i in self.preOrder():
            i._selected = False
        for i in self.search(spec):
            for j in i.pathToRoot():
                j._selected = True
            for j in i.preOrder():
                j._selected = True

    def printStructure(self, outf=sys.stdout):
        for i in self.preOrder():
            if i.name:
                parts = i.fullPathParts()
                if len(parts) > 1:
                    print >> outf, "    "*(len(parts)-1),
                print >> outf, i.name

AUTO = object()

class TestTree(_TestBase):
    """
        Automatically turns methods or arbitrary callables of the form test_*
        into TestNodes.
    """
    _testPrefix = "test_"
    _base = None
    _exclude = None
    _include = None
    name = None
    # An OK object if setUp succeeded, an Error object if it failed, and None
    # if no setUp was run.
    setUpState = None
    # An OK object if tearDown succeeded, an Error object if it failed, and None
    # if no tearDown was run.
    tearDownState = None
    # An OK object if setupAll succeeded, an Error object if it failed, and None
    # if no tearDown was run.
    setUpAllState = None
    # An OK object if teardownAll succeeded, an Error object if it failed, and
    # None if no tearDown was run.
    tearDownAllState = None
    # An OK object if run succeeded, an Error object if it failed. For a
    # TestTree object, this should never be an error.
    runState = None
    def __init__(self, children=None, name=AUTO):
        if self.name:
            name = self.name
        elif name is AUTO:
            name = self.__class__.__name__
        _TestBase.__init__(self, children, name)
        k = dir(self)
        k.sort()
        for i in k:
            if i.startswith(self._testPrefix):
                self.addChild(TestWrapper(i, getattr(self, i)))

    def _states(self):
        return [
                    self.setUpAllState,
                    self.tearDownAllState,
                    self.setUpState,
                    self.tearDownState,
                    self.runState,
                ]

    def run(self, output):
        """
            Run the tests contained in this suite.
        """
        self._run(self, self, "setUpAll")
        for i in self.children:
            self._run(self, i, "setUp")
            try:
                self._run(i, self, "run", output)
            except Skip:
                output.nodeError(i)
                return
            self._run(self, i, "tearDown")
        self._run(self, self, "tearDownAll")


class TestNode(_TestBase):
    # An OK object if run succeeded, an Error object if it failed, and None
    # if the test was not run.
    callState = None
    # An OK object if setUp succeeded, an Error object if it failed, and None
    # if no setUp was run.
    setUpState = None
    # An OK object if setUp succeeded, an Error object if it failed, and None
    # if no tearDown was run.
    tearDownState = None
    def __init__(self, name):
        _TestBase.__init__(self, None, name=name)

    def run(self, output):
        output.nodePre(self)
        try:
            self._run(self, self, "__call__")
        except Skip:
            output.nodeError(self)
            return
        output.nodePass(self)

    def _states(self):
        return [
                    self.setUpState,
                    self.tearDownState,
                    self.callState,
                ]

    def __call__(self):
        raise NotImplementedError


class TestWrapper(TestNode):
    def __init__(self, name, meth):
        TestNode.__init__(self, name)
        self.meth = meth

    def __call__(self):
        self.meth()

    def __repr__(self):
        return "TestWrapper: %s"%self.name


class FileNode(TestTree):
    # The special magic flag allows pry to run coverage analysis on its own 
    # test suite
    def __init__(self, dirname, filename, magic):
        modname = filename[:-3]
        TestTree.__init__(self, name=os.path.join(dirname, modname))
        self.dirname, self.filename = dirname, filename
        m = __import__(modname)
        # When pry starts up, it loads the libpry module. In order for the
        # instantiation stuff in libpry to be counted in coverage, we need to
        # go through and re-execute them. We don't "reload", since this will create
        # a new suite of class instances, and break our code.
        if magic:
            for k in sys.modules.keys():
                if "libpry" in k and sys.modules[k]:
                    n = sys.modules[k].__file__
                    if n.endswith("pyc"):
                        execfile(n[:-1])
                    elif n.endswith("py"):
                        execfile(n)
        # Force a reload to stop Python caching modules that happen to have 
        # the same name
        reload(m)
        if hasattr(m, "tests"):
            self.addChildrenFromList(m.tests)

    def __repr__(self):
        return "FileNode: %s"%self.filename


class DirNode(TestTree):
    CONF = ".pry"
    def __init__(self, path, cover):
        TestTree.__init__(self, name=None)
        if os.path.isdir(path):
            self.dirPath = path
            glob = _TestGlob
        elif os.path.isfile(path):
            self.dirPath = os.path.dirname(path) or "."
            glob = os.path.basename(path)

        c = config.Config(os.path.join(self.dirPath, self.CONF))
        self.baseDir = c.base
        self.coveragePath = c.coverage
        self.excludeList = c.exclude
        self.magic = c._magic

        self.coverage = False
        self._pre()
        if cover:
            self.coverage = coverage.Coverage(self.coveragePath, self.excludeList)
            self.coverage.start()
        for i in os.listdir("."):
            if fnmatch.fnmatch(i, glob):
                self.addChild(FileNode(path, i, self.magic))
        self._post()

    def _pre(self):
        self.oldPath = sys.path
        sys.path = sys.path[:]
        sys.path.insert(0, ".")
        sys.path.insert(0, self.baseDir)
        self.oldcwd = os.getcwd()
        os.chdir(self.dirPath)
        if self.coverage:
            self.coverage.start()
    
    def _post(self):
        sys.path = self.oldPath
        os.chdir(self.oldcwd)
        if self.coverage:
            self.coverage.stop()

    def setUpAll(self):
        self._pre()

    def tearDownAll(self):
        self._post()

    def __repr__(self):
        return "DirNode: %s"%self.dirPath


class RootNode(TestTree):
    """
        This node is the parent of all tests.
    """
    def __init__(self, cover):
        TestTree.__init__(self, name=None)
        self.cover = cover

    def addPath(self, path, recurse):
        if recurse:
            dirset = set()
            for root, dirs, files in os.walk(path):
                for i in files:
                    if fnmatch.fnmatch(i, _TestGlob):
                        dirset.add(root)
            for i in dirset:
                self.addChild(DirNode(i, self.cover))
        else:
            self.addChild(DirNode(path, self.cover))


