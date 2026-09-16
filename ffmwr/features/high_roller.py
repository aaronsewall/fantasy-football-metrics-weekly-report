__author__ = "Wren J. R. (uberfastman)"
__email__ = "uberfastman@uberfastman.dev"

from datetime import datetime
from pathlib import Path
import re
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup

from ffmwr.features.base.feature import BaseFeature
from ffmwr.utilities.constants import nfl_team_abbreviation_conversions, nfl_team_abbreviations
from ffmwr.utilities.logger import get_logger
from ffmwr.utilities.settings import AppSettings, get_app_settings_from_env_file
from ffmwr.utilities.utils import generate_normalized_player_key

logger = get_logger(__name__, propagate=False)


def _normalize_team_abbreviation(value: str) -> Optional[str]:
    """Return a known NFL abbreviation, or None for untrusted scraped text."""
    abbreviation = (value or "").strip().upper()
    abbreviation = nfl_team_abbreviation_conversions.get(abbreviation, abbreviation)
    return abbreviation if abbreviation in nfl_team_abbreviations else None


def _team_abbreviation_from_row(row, preferred_cell=None) -> Optional[str]:
    """Extract a team without relying on an image having visible text."""
    search_root = preferred_cell or row
    image = search_root.find("img")
    if image:
        for attribute in (
            "alt",
            "title",
            "data-team",
            "data-team-abbr",
            "data-team-abbreviation",
            "data-abbreviation",
        ):
            value = image.get(attribute, "")
            for candidate in [value, *re.findall(r"[A-Za-z]{2,4}", value)]:
                abbreviation = _normalize_team_abbreviation(candidate)
                if abbreviation:
                    return abbreviation
        for attribute in ("src", "data-src"):
            image_path = image.get(attribute, "")
            for candidate in re.findall(r"[A-Za-z]{2,4}", image_path):
                abbreviation = _normalize_team_abbreviation(candidate)
                if abbreviation:
                    return abbreviation

    # This also handles Spotrac's malformed/changed image markup.
    cells = preferred_cell.find_all("td") if preferred_cell else row.find_all("td")
    if preferred_cell:
        cells = [preferred_cell]
    for cell in cells:
        for candidate in re.findall(r"\b[A-Za-z]{2,4}\b", cell.get_text(" ", strip=True)):
            abbreviation = _normalize_team_abbreviation(candidate)
            if abbreviation:
                return abbreviation
    return None


def _cell_text(row, class_name: str) -> str:
    cell = row.find("td", class_=lambda classes: classes and class_name in classes)
    return cell.get_text(" ", strip=True) if cell else ""


class HighRollerFeature(BaseFeature):
    def __init__(
        self,
        season: int,
        week_for_report: int,
        data_dir: Path,
        refresh: bool = False,
        save_data: bool = False,
        offline: bool = False,
    ):
        """Initialize class, load data from Spotrac.com."""
        self.season: int = season

        defense = {
            "CB": "D",
            "DE": "D",
            "DT": "D",
            "FS": "D",
            "ILB": "D",
            "LB": "D",
            "OLB": "D",
            "S": "D",
            "SS": "D",
        }
        offense = {
            "FB": "O",
            "QB": "O",
            "RB": "O",
            "TE": "O",
            "WR": "O",
        }
        special_teams = {
            "K": "S",
            "P": "S",
        }
        offensive_line = {
            "C": "L",
            "G": "L",
            "LS": "L",
            "LT": "L",
            "RT": "L",
        }
        team_defense = {
            "D/ST": "D",
        }
        # position type reference
        self.position_types: Dict[str, str] = {
            **defense,
            **offense,
            **special_teams,
            **offensive_line,
            **team_defense,
        }

        super().__init__(
            "high_roller",
            f"https://www.spotrac.com/nfl/fines/_/year/{self.season}",
            week_for_report,
            data_dir,
            True,  # TODO: decide if team D/ST roll-ups should be included in high roller total
            refresh,
            save_data,
            offline,
        )

    # noinspection PyCallingNonCallable
    def _get_feature_data(self):
        for team in nfl_team_abbreviations:
            self.feature_data[team] = {
                "position": "D/ST",
                "players": {},
                "violators": [],
                "violators_count": 0,
                "fines_count": 0,
                "fines_total": 0.0,
                "worst_violation": None,
                "worst_violation_fine": 0.0,
            }

        user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/13.0.2 Safari/605.1.15"
        )
        headers = {"user-agent": user_agent}

        try:
            response = requests.get(self.feature_web_base_url, headers=headers, timeout=20)
        except requests.RequestException as error:
            logger.warning("Unable to retrieve High Roller data from Spotrac: %s", error)
            return
        response_text = getattr(response, "text", "") or ""
        status_code = getattr(response, "status_code", 0)
        if status_code < 200 or status_code >= 300 or not response_text.strip():
            logger.warning("Spotrac response is unavailable (HTTP %s); High Roller data is empty.", status_code)
            return
        html_soup = BeautifulSoup(response_text, "html.parser")
        logger.debug("Response URL: %s", getattr(response, "url", self.feature_web_base_url))
        logger.debug(f"Response (HTML):\n{html_soup.prettify()}")

        tbody = html_soup.find("tbody")
        if tbody is None:
            logger.warning("Spotrac response has no fines table; High Roller data is empty.")
            return
        fined_players = tbody.find_all("tr")

        parsed_rows = 0
        for row_number, player in enumerate(fined_players, 1):
            parsed = self._parse_row(player, row_number)
            if parsed is None:
                continue
            parsed_rows += 1
            player_full_name, player_team_abbr, player_position, player_position_type, player_fine_info = parsed

            normalized_player_key = generate_normalized_player_key(player_full_name, player_team_abbr)

            # add raw player data json to raw_player_data for reference
            self.raw_feature_data[normalized_player_key] = player.prettify()

            if normalized_player_key not in self.feature_data.keys():
                self.feature_data[normalized_player_key] = {
                    **self._get_feature_data_template(
                        player_full_name, player_team_abbr, player_position, player_position_type
                    ),
                    "fines": [player_fine_info],
                    "fines_count": 1,
                    "fines_total": player_fine_info["violation_fine"],
                    "worst_violation": player_fine_info["violation"],
                    "worst_violation_fine": player_fine_info["violation_fine"],
                }
            else:
                self.feature_data[normalized_player_key]["fines"].append(player_fine_info)
                self.feature_data[normalized_player_key]["fines"].sort(
                    key=lambda x: (-x["violation_fine"], -datetime.fromisoformat(x["violation_date"]).timestamp())
                )
                self.feature_data[normalized_player_key]["fines_count"] += 1
                self.feature_data[normalized_player_key]["fines_total"] += player_fine_info["violation_fine"]

                worst_violation = self.feature_data[normalized_player_key]["fines"][0]
                self.feature_data[normalized_player_key]["worst_violation"] = worst_violation["violation"]
                self.feature_data[normalized_player_key]["worst_violation_fine"] = worst_violation["violation_fine"]

        for player_key in self.feature_data.keys():
            if self.feature_data[player_key]["position"] != "D/ST":
                player_team_abbr = self.feature_data[player_key]["team_abbr"]

                if player_key not in self.feature_data[player_team_abbr]["players"]:
                    player = self.feature_data[player_key]
                    self.feature_data[player_team_abbr]["players"][player_key] = player
                    self.feature_data[player_team_abbr]["violators"].append(player["full_name"])
                    self.feature_data[player_team_abbr]["violators"] = list(
                        set(self.feature_data[player_team_abbr]["violators"])
                    )
                    self.feature_data[player_team_abbr]["violators_count"] = len(
                        self.feature_data[player_team_abbr]["violators"]
                    )
                    self.feature_data[player_team_abbr]["fines_count"] += player["fines_count"]
                    self.feature_data[player_team_abbr]["fines_total"] += player["fines_total"]
                    if player["worst_violation_fine"] >= self.feature_data[player_team_abbr]["worst_violation_fine"]:
                        self.feature_data[player_team_abbr]["worst_violation"] = player["worst_violation"]
                        self.feature_data[player_team_abbr]["worst_violation_fine"] = player["worst_violation_fine"]
        if parsed_rows == 0:
            logger.warning("Spotrac fines table contained no parseable rows; High Roller data is empty.")

    def _parse_row(self, row, row_number):
        player_cell = row.find("td", class_=lambda classes: classes and "fines-player" in classes)
        link = player_cell.find("a") if player_cell else None
        link = link or row.find("a", class_=lambda classes: classes and "link" in classes) or row.find("a")
        name = link.get_text(" ", strip=True) if link else ""
        if not name and player_cell:
            name = player_cell.get_text(" ", strip=True)
        team_cell = row.find("td", class_=lambda classes: classes and "fines-team" in classes)
        team = _team_abbreviation_from_row(row, team_cell) or _team_abbreviation_from_row(row)
        position = (_cell_text(row, "fines-position") or _cell_text(row, "details-sm")).upper()
        amount = _cell_text(row, "fines-amount") or _cell_text(row, "highlight")
        date_text = _cell_text(row, "fines-date") or _cell_text(row, "text-right")
        amount_match = re.search(r"\d[\d,]*(?:\.\d+)?", amount)
        parsed_date = None
        for date_format in ("%m/%d/%y", "%m/%d/%Y"):
            try:
                parsed_date = datetime.strptime(date_text, date_format)
                break
            except ValueError:
                pass
        problems = (["player"] if not name else []) + (["team"] if not team else [])
        problems += ["position"] if position not in self.position_types else []
        problems += ["amount"] if not amount_match else []
        problems += ["date"] if parsed_date is None else []
        if problems:
            logger.warning(
                "Skipping malformed Spotrac row %s (%s): %s", row_number, name or "unknown", ", ".join(problems)
            )
            return None
        violation_element = row.find("td", class_=lambda classes: classes and "fines-infraction" in classes)
        violation_element = violation_element or row.find(
            "span", class_=lambda classes: classes and "text-muted" in classes
        )
        violation = violation_element.get_text(" ", strip=True) if violation_element else None
        if violation and violation[:2] in ("- ", ": "):
            violation = violation[2:].strip()
        return name, team, position, self.position_types[position], {
            "violation": violation,
            "violation_fine": int(float(amount_match.group(0).replace(",", ""))),
            "violation_season": self.season,
            "violation_date": parsed_date.isoformat(),
        }

    def get_player_worst_violation(
        self, player_first_name: str, player_last_name: str, player_team_abbr: str, player_position: str
    ) -> str:
        return self._get_player_feature_stats(
            player_first_name, player_last_name, player_team_abbr, player_position, "worst_violation", str
        )

    def get_player_worst_violation_fine(
        self, player_first_name: str, player_last_name: str, player_team_abbr: str, player_position: str
    ) -> float:
        return self._get_player_feature_stats(
            player_first_name, player_last_name, player_team_abbr, player_position, "worst_violation_fine", float
        )

    def get_player_fines_total(
        self, player_first_name: str, player_last_name: str, player_team_abbr: str, player_position: str
    ) -> float:
        return self._get_player_feature_stats(
            player_first_name, player_last_name, player_team_abbr, player_position, "fines_total", float
        )

    def get_player_num_violators(
        self, player_first_name: str, player_last_name: str, player_team_abbr: str, player_position: str
    ) -> int:
        return self._get_player_feature_stats(
            player_first_name, player_last_name, player_team_abbr, player_position, "violators_count", int
        )


if __name__ == "__main__":
    local_root_directory = Path(__file__).parent.parent.parent

    local_settings: AppSettings = get_app_settings_from_env_file(local_root_directory / ".env")

    local_high_roller_feature = HighRollerFeature(
        local_settings.season,
        1,
        local_root_directory / local_settings.data_dir_path / "tests" / "feature_data",
        refresh=True,
        save_data=True,
        offline=False,
    )
