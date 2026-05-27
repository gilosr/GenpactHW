from evaluation.execution_accuracy import compare_result_sets


class TestCompareResultSets:
    def test_identical_results_match(self):
        a = [{"name": "Alice", "grade": 90}]
        b = [{"name": "Alice", "grade": 90}]
        assert compare_result_sets(a, b) is True

    def test_different_row_order_matches(self):
        a = [{"name": "Alice"}, {"name": "Bob"}]
        b = [{"name": "Bob"}, {"name": "Alice"}]
        assert compare_result_sets(a, b) is True

    def test_different_column_order_matches(self):
        a = [{"b": 2, "a": 1}]
        b = [{"a": 1, "b": 2}]
        assert compare_result_sets(a, b) is True

    def test_different_values_do_not_match(self):
        a = [{"count": 9}]
        b = [{"count": 15}]
        assert compare_result_sets(a, b) is False

    def test_different_row_counts_do_not_match(self):
        a = [{"id": 1}, {"id": 2}]
        b = [{"id": 1}]
        assert compare_result_sets(a, b) is False

    def test_empty_results_match(self):
        assert compare_result_sets([], []) is True

    def test_int_vs_float_equivalence(self):
        a = [{"val": 9}]
        b = [{"val": 9.0}]
        assert compare_result_sets(a, b) is True

    def test_different_column_names_do_not_match(self):
        a = [{"count_star": 9}]
        b = [{"total": 9}]
        assert compare_result_sets(a, b) is False

    def test_none_values_match(self):
        a = [{"grade": None}]
        b = [{"grade": None}]
        assert compare_result_sets(a, b) is True

    def test_duplicate_rows_matter(self):
        a = [{"id": 1}, {"id": 1}]
        b = [{"id": 1}]
        assert compare_result_sets(a, b) is False
