"""
Tests for demangler.
"""

from dataclasses import dataclass

from gnu2_demangler import parse


@dataclass
class CaseData:
    input: str
    expected: str
    expected_no_params: str

    def test(self):
        """
        Run the demangler on the input and verify output matches.
        """
        try:
            actual = str(parse(self.input))
        except Exception as e:
            raise AssertionError(f"Failed on input `{self.input}`") from e

        if not self.expected == actual:
            raise AssertionError(
                "\n"
                f"Input:    {self.input}\n"
                f"Expected: {self.expected}\n"
                f"Actual:   {actual}\n"
            )


def test_basic():
    """
    Test very basic mangled function names with no special cases.
    """
    test_data = [
        CaseData(
            input="saveOnQuitOverlay__Fv",
            expected="saveOnQuitOverlay(void)",
            expected_no_params="saveOnQuitOverlay",
        ),
        CaseData(
            input="textShake__FiPi",
            expected="textShake(int, int *)",
            expected_no_params="textShake",
        ),
        CaseData(
            input="InitRTState__5Shell",
            expected="Shell::InitRTState(void)",
            expected_no_params="Shell::InitRTState",
        ),
        CaseData(
            input="Check__6UArrayi",
            expected="UArray::Check(int)",
            expected_no_params="UArray::Check",
        ),
        CaseData(
            input="updateBlimpWeaponState__16PrisonLevelSoundii",
            expected="PrisonLevelSound::updateBlimpWeaponState(int, int)",
            expected_no_params="PrisonLevelSound::updateBlimpWeaponState",
        ),
        CaseData(input="Round__Ff", expected="Round(float)", expected_no_params="Round"),
    ]

    for test in test_data:
        test.test()


def test_basic_tricky():
    """
    Test fairly basic mangled function names which use tricky characters, like
    qualified class names.
    """
    test_data = [
        CaseData(
            input="AddAlignment__9ivTSolverUiP12ivInteractorP7ivTGlue",
            expected="ivTSolver::AddAlignment(unsigned int, ivInteractor *, ivTGlue *)",
            expected_no_params="ivTSolver::AddAlignment",
        ),
        CaseData(
            input="ArrowheadIntersects__9ArrowLineP9ArrowheadR6BoxObjP7Graphic",
            expected="ArrowLine::ArrowheadIntersects(Arrowhead *, BoxObj &, Graphic *)",
            expected_no_params="ArrowLine::ArrowheadIntersects",
        ),
        CaseData(
            input="AtEnd__13ivRubberGroup",
            expected="ivRubberGroup::AtEnd(void)",
            expected_no_params="ivRubberGroup::AtEnd",
        ),
        CaseData(
            input="BgFilter__9ivTSolverP12ivInteractor",
            expected="ivTSolver::BgFilter(ivInteractor *)",
            expected_no_params="ivTSolver::BgFilter",
        ),
        CaseData(
            input="CoreConstDecls__8TextCodeR7ostream",
            expected="TextCode::CoreConstDecls(ostream &)",
            expected_no_params="TextCode::CoreConstDecls",
        ),
        CaseData(
            input="CoreConstDecls__8TextCodeO7ostream",
            expected="TextCode::CoreConstDecls(ostream &&)",
            expected_no_params="TextCode::CoreConstDecls",
        ),
        CaseData(
            input="Detach__8StateVarP12StateVarView",
            expected="StateVar::Detach(StateVarView *)",
            expected_no_params="StateVar::Detach",
        ),
        CaseData(
            input="Done__9ComponentG8Iterator",
            expected="Component::Done(Iterator)",
            expected_no_params="Component::Done",
        ),
        CaseData(
            input="Effect__11RelateManipR7ivEvent",
            expected="RelateManip::Effect(ivEvent &)",
            expected_no_params="RelateManip::Effect",
        ),
        CaseData(
            input="Effect__11RelateManipO7ivEvent",
            expected="RelateManip::Effect(ivEvent &&)",
            expected_no_params="RelateManip::Effect",
        ),
        CaseData(
            input="IsAGroup__FP11GraphicViewP11GraphicComp",
            expected="IsAGroup(GraphicView *, GraphicComp *)",
            expected_no_params="IsAGroup",
        ),
        CaseData(
            input="IsA__10ButtonCodeUl",
            expected="ButtonCode::IsA(unsigned long)",
            expected_no_params="ButtonCode::IsA",
        ),
        CaseData(
            input="ReadName__FR7istreamPc",
            expected="ReadName(istream &, char *)",
            expected_no_params="ReadName",
        ),
        CaseData(
            input="Redraw__13StringBrowseriiii",
            expected="StringBrowser::Redraw(int, int, int, int)",
            expected_no_params="StringBrowser::Redraw",
        ),
        CaseData(
            input="Rotate__13ivTransformerf",
            expected="ivTransformer::Rotate(float)",
            expected_no_params="ivTransformer::Rotate",
        ),
        CaseData(
            input="SetExport__16MemberSharedNameUi",
            expected="MemberSharedName::SetExport(unsigned int)",
            expected_no_params="MemberSharedName::SetExport",
        ),
        CaseData(
            input="InsertBody__15H_PullrightMenuii",
            expected="H_PullrightMenu::InsertBody(int, int)",
            expected_no_params="H_PullrightMenu::InsertBody",
        ),
        CaseData(
            input="InsertCharacter__9TextManipc",
            expected="TextManip::InsertCharacter(char)",
            expected_no_params="TextManip::InsertCharacter",
        ),
        CaseData(
            input="Set__5DFacePcii",
            expected="DFace::Set(char *, int, int)",
            expected_no_params="DFace::Set",
        ),
    ]

    for test in test_data:
        test.test()


def test_multi_memory():
    """
    Verify that parameters with multiple memory tokens are printed with no spaces inbetween.
    """
    test_data = [
        CaseData(
            input="FindFixed__FRP4CNetP4CNet",
            expected="FindFixed(CNet *&, CNet *)",
            expected_no_params="FindFixed",
        ),
        CaseData(
            input="FindFixed__FOP4CNetP4CNet",
            expected="FindFixed(CNet *&&, CNet *)",
            expected_no_params="FindFixed",
        ),
    ]

    for test in test_data:
        test.test()


def test_single_underscores():
    """
    Verify that a symbol with single underscores (a realistic symbol name) does not
    trip up the demangler.
    """
    test_data = [
        CaseData(
            input="Fix48_abort__FR8twolongs",
            expected="Fix48_abort(twolongs &)",
            expected_no_params="Fix48_abort",
        ),
        CaseData(
            input="Fix48_abort__FO8twolongs",
            expected="Fix48_abort(twolongs &&)",
            expected_no_params="Fix48_abort",
        ),
    ]

    for test in test_data:
        test.test()


def test_const_member_func():
    """
    Verify that a `const` member function has the `const` qualifier printed at the
    correct location in the demangled string.
    """
    test_data = [
        CaseData(
            input="GetBgColor__C9ivPainter",
            expected="ivPainter::GetBgColor(void) const",
            expected_no_params="ivPainter::GetBgColor",
        ),
        CaseData(
            input="Rotated__C13ivTransformerf",
            expected="ivTransformer::Rotated(float) const",
            expected_no_params="ivTransformer::Rotated",
        ),
    ]

    for test in test_data:
        test.test()


def test_enum_argument():
    """
    Verify that an enum argument doesn't get pulled into the function's qualified name.
    """
    test_data = [
        CaseData(
            input="Set__14ivControlState13ControlStatusUi",
            expected="ivControlState::Set(ControlStatus, unsigned int)",
            expected_no_params="ivControlState::Set",
        ),
    ]

    for test in test_data:
        test.test()


def test_operator_overload():
    """
    Verify that operator overloads are demangled correctly.
    """
    test_data = [
        CaseData(
            input="__aml__5Fix16i",
            expected="Fix16::operator*=(int)",
            expected_no_params="Fix16::operator*=",
        ),
    ]

    for test in test_data:
        test.test()


# [
#     CaseData(
#         input="GetBarInfo__15iv2_6_VScrollerP13ivPerspectiveRiT2",
#         expected="iv2_6_VScroller::GetBarInfo(ivPerspective *, int &, int &)",
#         expected_no_params="iv2_6_VScroller::GetBarInfo",
#     ),
#     CaseData(
#         input="GetBarInfo__15iv2_6_VScrollerP13ivPerspectiveOiT2",
#         expected="iv2_6_VScroller::GetBarInfo(ivPerspective *, int &&, int &&)",
#         expected_no_params="iv2_6_VScroller::GetBarInfo",
#     ),
#     CaseData(
#         input="InsertToplevel__7ivWorldP12ivInteractorT1",
#         expected="ivWorld::InsertToplevel(ivInteractor *, ivInteractor *)",
#         expected_no_params="ivWorld::InsertToplevel",
#     ),
#     CaseData(
#         input="InsertToplevel__7ivWorldP12ivInteractorT1iiUi",
#         expected="ivWorld::InsertToplevel(ivInteractor *, ivInteractor *, int, int, unsigned int)",
#         expected_no_params="ivWorld::InsertToplevel",
#     ),
#     CaseData(
#         input="VConvert__9ivTSolverP12ivInteractorRP8TElementT2",
#         expected="ivTSolver::VConvert(ivInteractor *, TElement *&, TElement *&)",
#         expected_no_params="ivTSolver::VConvert",
#     ),
#     CaseData(
#         input="VConvert__9ivTSolverP7ivTGlueRP8TElement",
#         expected="ivTSolver::VConvert(ivTGlue *, TElement *&)",
#         expected_no_params="ivTSolver::VConvert",
#     ),
#     CaseData(
#         input="VOrder__9ivTSolverUiRP12ivInteractorT2",
#         expected="ivTSolver::VOrder(unsigned int, ivInteractor *&, ivInteractor *&)",
#         expected_no_params="ivTSolver::VOrder",
#     ),
#     CaseData(
#         input="_10PageButton$__both",
#         expected="PageButton::__both",
#         expected_no_params="PageButton::__both",
#     ),
#     CaseData(
#         input="_3RNG$singleMantissa",
#         expected="RNG::singleMantissa",
#         expected_no_params="RNG::singleMantissa",
#     ),
#     CaseData(
#         input="_5IComp$_release", expected="IComp::_release", expected_no_params="IComp::_release"
#     ),
#     CaseData(
#         input="_$_10BitmapComp",
#         expected="BitmapComp::~BitmapComp(void)",
#         expected_no_params="BitmapComp::~BitmapComp",
#     ),
#     CaseData(
#         input="_$_9__io_defs",
#         expected="__io_defs::~__io_defs(void)",
#         expected_no_params="__io_defs::~__io_defs",
#     ),
#     CaseData(
#         input="_$_Q23foo3bar", expected="foo::bar::~bar(void)", expected_no_params="foo::bar::~bar"
#     ),
#     CaseData(
#         input="_$_Q33foo3bar4bell",
#         expected="foo::bar::bell::~bell(void)",
#         expected_no_params="foo::bar::bell::~bell",
#     ),
#     CaseData(
#         input="__10ivTelltaleiP7ivGlyph",
#         expected="ivTelltale::ivTelltale(int, ivGlyph *)",
#         expected_no_params="ivTelltale::ivTelltale",
#     ),
#     CaseData(
#         input="__10ivViewportiP12ivInteractorUi",
#         expected="ivViewport::ivViewport(int, ivInteractor *, unsigned int)",
#         expected_no_params="ivViewport::ivViewport",
#     ),
#     CaseData(
#         input="__10ostrstream",
#         expected="ostrstream::ostrstream(void)",
#         expected_no_params="ostrstream::ostrstream",
#     ),
#     CaseData(
#         input="__10ostrstreamPcii",
#         expected="ostrstream::ostrstream(char *, int, int)",
#         expected_no_params="ostrstream::ostrstream",
#     ),
#     CaseData(
#         input="__11BitmapTablei",
#         expected="BitmapTable::BitmapTable(int)",
#         expected_no_params="BitmapTable::BitmapTable",
#     ),
#     CaseData(
#         input="__12ViewportCodeP12ViewportComp",
#         expected="ViewportCode::ViewportCode(ViewportComp *)",
#         expected_no_params="ViewportCode::ViewportCode",
#     ),
#     CaseData(
#         input="__12iv2_6_Borderii",
#         expected="iv2_6_Border::iv2_6_Border(int, int)",
#         expected_no_params="iv2_6_Border::iv2_6_Border",
#     ),
#     CaseData(
#         input="__12ivBreak_Listl",
#         expected="ivBreak_List::ivBreak_List(long)",
#         expected_no_params="ivBreak_List::ivBreak_List",
#     ),
#     CaseData(
#         input="__14iv2_6_MenuItemiP12ivInteractor",
#         expected="iv2_6_MenuItem::iv2_6_MenuItem(int, ivInteractor *)",
#         expected_no_params="iv2_6_MenuItem::iv2_6_MenuItem",
#     ),
#     CaseData(
#         input="__20DisplayList_IteratorR11DisplayList",
#         expected="DisplayList_Iterator::DisplayList_Iterator(DisplayList &)",
#         expected_no_params="DisplayList_Iterator::DisplayList_Iterator",
#     ),
#     CaseData(input="__3fooRT0", expected="foo::foo(foo &)", expected_no_params="foo::foo"),
#     CaseData(
#         input="__3fooiN31", expected="foo::foo(int, int, int, int)", expected_no_params="foo::foo"
#     ),
#     CaseData(
#         input="__3fooiRT0iT2iT2",
#         expected="foo::foo(int, foo &, int, foo &, int, foo &)",
#         expected_no_params="foo::foo",
#     ),
#     CaseData(
#         input="__6KeyMapPT0",
#         expected="KeyMap::KeyMap(KeyMap *)",
#         expected_no_params="KeyMap::KeyMap",
#     ),
#     CaseData(
#         input="__8ArrowCmdP6EditorUiUi",
#         expected="ArrowCmd::ArrowCmd(Editor *, unsigned int, unsigned int)",
#         expected_no_params="ArrowCmd::ArrowCmd",
#     ),
#     CaseData(
#         input="__9F_EllipseiiiiP7Graphic",
#         expected="F_Ellipse::F_Ellipse(int, int, int, int, Graphic *)",
#         expected_no_params="F_Ellipse::F_Ellipse",
#     ),
#     CaseData(
#         input="__9FrameDataP9FrameCompi",
#         expected="FrameData::FrameData(FrameComp *, int)",
#         expected_no_params="FrameData::FrameData",
#     ),
#     CaseData(
#         input="__9HVGraphicP9CanvasVarP7Graphic",
#         expected="HVGraphic::HVGraphic(CanvasVar *, Graphic *)",
#         expected_no_params="HVGraphic::HVGraphic",
#     ),
#     CaseData(
#         input="__Q23foo3bar", expected="foo::bar::bar(void)", expected_no_params="foo::bar::bar"
#     ),
#     CaseData(
#         input="__Q33foo3bar4bell",
#         expected="foo::bar::bell::bell(void)",
#         expected_no_params="foo::bar::bell::bell",
#     ),
#     CaseData(
#         input="__aa__3fooRT0",
#         expected="foo::operator&&(foo &)",
#         expected_no_params="foo::operator&&",
#     ),
#     CaseData(
#         input="__aad__3fooRT0",
#         expected="foo::operator&=(foo &)",
#         expected_no_params="foo::operator&=",
#     ),
#     CaseData(
#         input="__ad__3fooRT0", expected="foo::operator&(foo &)", expected_no_params="foo::operator&"
#     ),
#     CaseData(
#         input="__adv__3fooRT0",
#         expected="foo::operator/=(foo &)",
#         expected_no_params="foo::operator/=",
#     ),
#     CaseData(
#         input="__aer__3fooRT0",
#         expected="foo::operator^=(foo &)",
#         expected_no_params="foo::operator^=",
#     ),
#     CaseData(
#         input="__als__3fooRT0",
#         expected="foo::operator<<=(foo &)",
#         expected_no_params="foo::operator<<=",
#     ),
#     CaseData(
#         input="__amd__3fooRT0",
#         expected="foo::operator%=(foo &)",
#         expected_no_params="foo::operator%=",
#     ),
#     CaseData(
#         input="__ami__3fooRT0",
#         expected="foo::operator-=(foo &)",
#         expected_no_params="foo::operator-=",
#     ),
#     CaseData(
#         input="__aml__3FixRT0",
#         expected="Fix::operator*=(Fix &)",
#         expected_no_params="Fix::operator*=",
#     ),
#     CaseData(
#         input="__aml__5Fix32RT0",
#         expected="Fix32::operator*=(Fix32 &)",
#         expected_no_params="Fix32::operator*=",
#     ),
#     CaseData(
#         input="__aor__3fooRT0",
#         expected="foo::operator|=(foo &)",
#         expected_no_params="foo::operator|=",
#     ),
#     CaseData(
#         input="__apl__3fooRT0",
#         expected="foo::operator+=(foo &)",
#         expected_no_params="foo::operator+=",
#     ),
#     CaseData(
#         input="__ars__3fooRT0",
#         expected="foo::operator>>=(foo &)",
#         expected_no_params="foo::operator>>=",
#     ),
#     CaseData(
#         input="__as__3fooRT0", expected="foo::operator=(foo &)", expected_no_params="foo::operator="
#     ),
#     CaseData(
#         input="__cl__3fooRT0",
#         expected="foo::operator()(foo &)",
#         expected_no_params="foo::operator()",
#     ),
#     CaseData(
#         input="__cl__6Normal",
#         expected="Normal::operator()(void)",
#         expected_no_params="Normal::operator()",
#     ),
#     CaseData(
#         input="__cl__6Stringii",
#         expected="String::operator()(int, int)",
#         expected_no_params="String::operator()",
#     ),
#     CaseData(
#         input="__cm__3fooRT0",
#         expected="foo::operator, (foo &)",
#         expected_no_params="foo::operator,",
#     ),
#     CaseData(
#         input="__co__3foo", expected="foo::operator~(void)", expected_no_params="foo::operator~"
#     ),
#     CaseData(
#         input="__dl__3fooPv",
#         expected="foo::operator delete(void *)",
#         expected_no_params="foo::operator delete",
#     ),
#     CaseData(
#         input="__dv__3fooRT0", expected="foo::operator/(foo &)", expected_no_params="foo::operator/"
#     ),
#     CaseData(
#         input="__eq__3fooRT0",
#         expected="foo::operator==(foo &)",
#         expected_no_params="foo::operator==",
#     ),
# ]

# TEST_DATA_FILE = Path("tests/test_data.json")

# def test_demangler():
#     """
#     Run the original GNU demangler test suite from binutils and verify each
#     symbol is demangled to the expected name/parameters.
#     """

#     with TEST_DATA_FILE.open("r") as f:
#         test_data: list[dict[str, str]] = json.load(f)

#     for test in test_data:
#         # The test data only contains valid demangler cases right now, so we should
#         # fail if exceptions occur.
#         actual = str(parse(test["input"]))
#         assert test["expected"] == actual
