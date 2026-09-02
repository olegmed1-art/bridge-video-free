// Minimal deterministic DDS 3.x wrapper for Bridge School golden CI.
#include <algorithm>
#include <cctype>
#include <cstring>
#include <iostream>
#include <string>
#include <api/dll.h>

namespace {
auto upper(std::string value) -> std::string {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
  return value;
}
auto parse_dealer(const std::string& value) -> int {
  const std::string dealers = "NESW";
  if (value.size() != 1) return -1;
  const auto pos = dealers.find(static_cast<char>(std::toupper(value[0])));
  return pos == std::string::npos ? -1 : static_cast<int>(pos);
}
auto parse_vulnerability(const std::string& value) -> int {
  const std::string v = upper(value);
  if (v == "NONE" || v == "LOVE" || v == "-") return 0;
  if (v == "ALL" || v == "BOTH") return 1;
  if (v == "NS" || v == "N-S") return 2;
  if (v == "EW" || v == "E-W") return 3;
  return -1;
}
auto print_error(int code) -> int {
  char message[80] = {};
  ErrorMessage(code, message);
  std::cerr << "DDS error " << code << ": " << message << '\n';
  return 2;
}
}

auto main(int argc, char** argv) -> int {
  if (argc != 4) return 1;
  const int dealer = parse_dealer(argv[1]);
  const int vulnerability = parse_vulnerability(argv[2]);
  const std::string pbn = argv[3];
  if (dealer < 0 || vulnerability < 0 || pbn.empty() ||
      pbn.size() >= sizeof(DdTableDealPBN::cards)) return 1;
  SetMaxThreads(0);
  DdTableDealPBN deal{};
  std::memcpy(deal.cards, pbn.c_str(), pbn.size() + 1);
  DdTableResults table{};
  int result = CalcDDtablePBN(deal, &table);
  if (result != RETURN_NO_FAULT) return print_error(result);
  ParResultsDealer par{};
  result = DealerPar(&table, &par, dealer, vulnerability);
  if (result != RETURN_NO_FAULT) return print_error(result);
  const char* strains[5] = {"S", "H", "D", "C", "NT"};
  std::cout << "{\"hand_order\":[\"N\",\"E\",\"S\",\"W\"],";
  std::cout << "\"strain_order\":[\"S\",\"H\",\"D\",\"C\",\"NT\"],\"dd_table\":{";
  for (int strain = 0; strain < 5; ++strain) {
    if (strain) std::cout << ',';
    std::cout << '\"' << strains[strain] << "\":[";
    for (int hand = 0; hand < 4; ++hand) {
      if (hand) std::cout << ',';
      std::cout << table.res_table[strain][hand];
    }
    std::cout << ']';
  }
  std::cout << "},\"par_score_ns\":" << par.score << ",\"par_contracts\":[";
  for (int i = 0; i < par.number; ++i) {
    if (i) std::cout << ',';
    std::cout << '\"' << par.contracts[i] << '\"';
  }
  std::cout << "]}\n";
  return 0;
}
