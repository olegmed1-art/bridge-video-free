#include <api/PBN.h>
#include <api/dds.h>
#include <api/dll.h>
#include <api/solve_board.hpp>
#include <solver_context/solver_context.hpp>

#include <array>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

struct CanonicalFutureTricks {
  int cards{};
  std::array<int, 13> suit{};
  std::array<int, 13> rank{};
  std::array<int, 13> equals{};
  std::array<int, 13> score{};
};

CanonicalFutureTricks canonicalize(const FutureTricks& fut) {
  CanonicalFutureTricks out{};
  out.cards = fut.cards;
  for (int i = 0; i < fut.cards && i < 13; ++i) {
    out.suit[i] = fut.suit[i];
    out.rank[i] = fut.rank[i];
    out.equals[i] = fut.equals[i];
    out.score[i] = fut.score[i];
  }
  return out;
}

bool same_result(const CanonicalFutureTricks& a, const CanonicalFutureTricks& b) {
  if (a.cards != b.cards) return false;
  for (int i = 0; i < a.cards; ++i) {
    if (a.suit[i] != b.suit[i] || a.rank[i] != b.rank[i] ||
        a.equals[i] != b.equals[i] || a.score[i] != b.score[i]) {
      return false;
    }
  }
  return true;
}

void print_int_array(const std::array<int, 13>& values, int count) {
  std::cout << '[';
  for (int i = 0; i < count; ++i) {
    if (i) std::cout << ',';
    std::cout << values[i];
  }
  std::cout << ']';
}

void print_result(const CanonicalFutureTricks& result) {
  std::cout << "{\"cards\":" << result.cards << ",\"suit\":";
  print_int_array(result.suit, result.cards);
  std::cout << ",\"rank\":";
  print_int_array(result.rank, result.cards);
  std::cout << ",\"equals\":";
  print_int_array(result.equals, result.cards);
  std::cout << ",\"score\":";
  print_int_array(result.score, result.cards);
  std::cout << '}';
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 3) {
    std::cerr << "usage: dds_stage2_context_gate <deal_id_sha256> <pbn-deal>\n";
    return 2;
  }

  const std::string deal_id = argv[1];
  const std::string pbn = argv[2];
  if (deal_id.size() != 64 || pbn.empty()) {
    std::cerr << "invalid DealID or PBN input\n";
    return 2;
  }

  // DDS uses 0=S,1=H,2=D,3=C,4=NT and hands 0=N,1=E,2=S,3=W.
  Deal deal{};
  deal.trump = 4;  // NT, chosen only to make this deterministic engine gate concrete.
  deal.first = 0;  // North to lead.
  std::memset(deal.currentTrickSuit, 0, sizeof(deal.currentTrickSuit));
  std::memset(deal.currentTrickRank, 0, sizeof(deal.currentTrickRank));

  if (convert_from_pbn(pbn.c_str(), deal.remainCards) != 1) {
    std::cerr << "PBN conversion failed\n";
    return 3;
  }

  SetMaxThreads(1);

  SolverContext ctx;
  const bool tt_lazy_before_solve = (ctx.maybe_trans_table() == nullptr);

  FutureTricks first{};
  const int rc1 = solve_board(ctx, deal, -1, 3, 0, &first);
  if (rc1 != RETURN_NO_FAULT) {
    std::cerr << "first solve failed rc=" << rc1 << '\n';
    return 4;
  }

  auto* tt_after_first = ctx.maybe_trans_table();
  const bool tt_created_by_solve = (tt_after_first != nullptr);
  const auto first_result = canonicalize(first);

  FutureTricks second{};
  const int rc2 = solve_board(ctx, deal, -1, 3, 0, &second);
  if (rc2 != RETURN_NO_FAULT) {
    std::cerr << "second solve failed rc=" << rc2 << '\n';
    return 5;
  }

  auto* tt_after_second = ctx.maybe_trans_table();
  const bool same_context_tt_instance =
      tt_after_first != nullptr && tt_after_first == tt_after_second;
  const auto second_result = canonicalize(second);
  const bool repeated_solve_result_equal = same_result(first_result, second_result);

  // Upstream DDS3 defines SolverContext instances created from the same ThreadData as
  // sharing the same transposition-table registry entry. Verify that property directly
  // instead of inferring it from timing or node-count changes.
  auto shared_thread = ctx.thread();
  SolverContext sibling{shared_thread};
  const bool sibling_sees_same_tt =
      tt_after_second != nullptr && sibling.maybe_trans_table() == tt_after_second;

  if (!tt_created_by_solve || !same_context_tt_instance || !sibling_sees_same_tt ||
      !repeated_solve_result_equal) {
    std::cerr << "DDS Stage-2 context/TT invariant failed\n";
    return 6;
  }

  std::cout << '{'
            << "\"gate_version\":\"stage2-dds-context-v1\"," 
            << "\"deal_id\":\"" << deal_id << "\"," 
            << "\"dds_upstream_commit\":\"cdd13cf5b700788ac8c1391501b42445b3129b45\"," 
            << "\"solver_api\":\"solve_board(SolverContext&)\"," 
            << "\"trump\":4,\"first\":0,\"target\":-1,\"solutions\":3,\"mode\":0,"
            << "\"tt_lazy_before_solve\":" << (tt_lazy_before_solve ? "true" : "false") << ','
            << "\"tt_created_by_solve\":" << (tt_created_by_solve ? "true" : "false") << ','
            << "\"same_context_tt_instance\":" << (same_context_tt_instance ? "true" : "false") << ','
            << "\"sibling_context_same_thread_shares_tt\":" << (sibling_sees_same_tt ? "true" : "false") << ','
            << "\"repeated_solve_result_equal\":" << (repeated_solve_result_equal ? "true" : "false") << ','
            << "\"nodes_first\":" << first.nodes << ','
            << "\"nodes_second\":" << second.nodes << ','
            << "\"result\":";
  print_result(first_result);
  std::cout << "}\n";

  return 0;
}
