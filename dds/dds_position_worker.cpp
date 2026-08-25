#include <api/PBN.h>
#include <api/dds.h>
#include <api/dll.h>
#include <api/solve_board.hpp>
#include <solver_context/solver_context.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <cstring>
#include <iostream>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
int parse_suit(char c) {
  switch (static_cast<char>(std::toupper(static_cast<unsigned char>(c)))) {
    case 'S': return 0;
    case 'H': return 1;
    case 'D': return 2;
    case 'C': return 3;
    default: throw std::runtime_error("invalid suit");
  }
}

int parse_rank(std::string value) {
  for (auto& c : value) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
  if (value == "A") return 14;
  if (value == "K") return 13;
  if (value == "Q") return 12;
  if (value == "J") return 11;
  if (value == "T" || value == "10") return 10;
  if (value.size() == 1 && value[0] >= '2' && value[0] <= '9') return value[0] - '0';
  throw std::runtime_error("invalid rank");
}

int parse_trump(std::string value) {
  for (auto& c : value) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
  if (value == "S") return 0;
  if (value == "H") return 1;
  if (value == "D") return 2;
  if (value == "C") return 3;
  if (value == "NT" || value == "N") return 4;
  throw std::runtime_error("invalid trump");
}

int parse_hand(std::string value) {
  for (auto& c : value) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
  if (value == "N") return 0;
  if (value == "E") return 1;
  if (value == "S") return 2;
  if (value == "W") return 3;
  throw std::runtime_error("invalid first hand");
}

std::string rank_text(int rank) {
  if (rank == 14) return "A";
  if (rank == 13) return "K";
  if (rank == 12) return "Q";
  if (rank == 11) return "J";
  if (rank == 10) return "T";
  return std::to_string(rank);
}

std::string card_text(int suit, int rank) {
  static constexpr std::array<char, 4> suits{'S', 'H', 'D', 'C'};
  if (suit < 0 || suit > 3) throw std::runtime_error("DDS3 returned invalid suit");
  return std::string(1, suits[static_cast<std::size_t>(suit)]) + rank_text(rank);
}

std::vector<std::string> split_tabs(const std::string& line) {
  std::vector<std::string> fields;
  std::size_t start = 0;
  while (true) {
    auto pos = line.find('\t', start);
    if (pos == std::string::npos) {
      fields.push_back(line.substr(start));
      break;
    }
    fields.push_back(line.substr(start, pos - start));
    start = pos + 1;
  }
  return fields;
}

int set_current_trick(Deal& deal, const std::string& encoded) {
  std::memset(deal.currentTrickSuit, 0, sizeof(deal.currentTrickSuit));
  std::memset(deal.currentTrickRank, 0, sizeof(deal.currentTrickRank));
  if (encoded.empty() || encoded == "-") return 0;
  std::stringstream ss(encoded);
  std::string token;
  int count = 0;
  while (std::getline(ss, token, ',')) {
    if (++count > 3) throw std::runtime_error("current trick has more than three cards");
    if (token.size() < 2) throw std::runtime_error("invalid current trick card");
    deal.currentTrickSuit[count - 1] = parse_suit(token[0]);
    deal.currentTrickRank[count - 1] = parse_rank(token.substr(1));
  }
  return count;
}

int count_remaining_cards(const Deal& deal) {
  int total = 0;
  for (int hand = 0; hand < 4; ++hand)
    for (int suit = 0; suit < 4; ++suit)
      total += __builtin_popcount(deal.remainCards[hand][suit]);
  return total;
}

void print_error(const std::string& code) {
  std::cout << "{\"ok\":false,\"engine\":\"DDS3\",\"fallback_used\":false,\"error\":\""
            << code << "\"}\n" << std::flush;
}

void print_moves(const FutureTricks& fut, std::size_t request_seq, int tricks_remaining,
                 bool tt_present_before, bool tt_present_after, bool same_tt) {
  struct Row { std::string card; int tricks; bool equivalent; std::string representative; };
  std::vector<Row> rows;
  std::set<std::string> seen;
  for (int i = 0; i < fut.cards; ++i) {
    const auto representative = card_text(fut.suit[i], fut.rank[i]);
    if (seen.insert(representative).second)
      rows.push_back({representative, fut.score[i], false, representative});
    const int equals = fut.equals[i];
    // DDS public holding masks use the absolute rank as the bit index. The
    // solver stores MoveType::sequence in the internal rank-2 layout and
    // restores the public layout as `sequence << 2` in FutureTricks.equals.
    for (int rank = 2; rank <= 14; ++rank) {
      if ((equals & (1 << rank)) == 0) continue;
      const auto equal_card = card_text(fut.suit[i], rank);
      if (seen.insert(equal_card).second)
        rows.push_back({equal_card, fut.score[i], true, representative});
    }
  }
  std::stable_sort(rows.begin(), rows.end(), [](const Row& a, const Row& b) {
    if (a.tricks != b.tricks) return a.tricks > b.tricks;
    return a.card < b.card;
  });

  std::cout << "{\"ok\":true,\"engine\":\"DDS3\",\"fallback_used\":false,"
            << "\"operation\":\"position_all_moves\","
            << "\"request_seq\":" << request_seq << ','
            << "\"tricks_remaining\":" << tricks_remaining << ','
            << "\"nodes\":" << fut.nodes << ','
            << "\"tt_present_before\":" << (tt_present_before ? "true" : "false") << ','
            << "\"tt_present_after\":" << (tt_present_after ? "true" : "false") << ','
            << "\"same_tt_instance\":" << (same_tt ? "true" : "false") << ','
            << "\"moves\":[";
  for (std::size_t i = 0; i < rows.size(); ++i) {
    if (i) std::cout << ',';
    std::cout << "{\"card\":\"" << rows[i].card << "\",\"tricks_for_side_to_play\":"
              << rows[i].tricks << ",\"equivalent\":" << (rows[i].equivalent ? "true" : "false")
              << ",\"representative\":\"" << rows[i].representative << "\"}";
  }
  std::cout << "]}\n" << std::flush;
}
}  // namespace

int main() {
  SetMaxThreads(1);
  SolverContext context;
  std::size_t request_seq = 0;
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.empty()) continue;
    try {
      const auto fields = split_tabs(line);
      if (fields.size() != 5 || fields[0] != "POSITION")
        throw std::runtime_error("invalid worker protocol");

      Deal deal{};
      deal.trump = parse_trump(fields[1]);
      deal.first = parse_hand(fields[2]);
      const int current_trick_count = set_current_trick(deal, fields[3]);
      if (fields[4].empty() || convert_from_pbn(fields[4].c_str(), deal.remainCards) != 1)
        throw std::runtime_error("PBN conversion failed");
      const int cards_in_hands = count_remaining_cards(deal);
      if ((cards_in_hands + current_trick_count) % 4 != 0)
        throw std::runtime_error("position card count is inconsistent");
      const int tricks_remaining = (cards_in_hands + current_trick_count) / 4;
      if (tricks_remaining < 1 || tricks_remaining > 13)
        throw std::runtime_error("invalid tricks remaining");

      auto* before = context.maybe_trans_table();
      FutureTricks fut{};
      int rc = solve_board(context, deal, -1, 3, 0, &fut);
      if (rc != RETURN_NO_FAULT) {
        print_error("DDS_SOLVE_FAILED_" + std::to_string(rc));
        continue;
      }
      if (fut.cards == 1 && fut.score[0] == -2) {
        FutureTricks scored{};
        rc = solve_board(context, deal, -1, 3, 1, &scored);
        if (rc != RETURN_NO_FAULT) {
          print_error("DDS_SCORE_FAILED_" + std::to_string(rc));
          continue;
        }
        fut = scored;
      }
      auto* after = context.maybe_trans_table();
      ++request_seq;
      print_moves(fut, request_seq, tricks_remaining, before != nullptr, after != nullptr,
                  before != nullptr && before == after);
    } catch (const std::exception& exc) {
      print_error(exc.what());
    }
  }
  return 0;
}
